"""魔改 2 · 执行失败错误分类：taxonomy 映射 + 两条执行路径的观察打标（error_class）。

约定：观察 dict 的 ``error_class`` 键**只在失败时存在**（「缺 = 成功」），
模型可读的分类与引导文案拼在 ``exception_info`` 里（<exception> 渲染通道）。
"""

import subprocess

import pytest
from pydantic import BaseModel, Field

import minisweagent.models.utils.tool_registry as tr_module
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.utils.tool_registry import ToolRegistry, ToolSpec, get_default_registry
from minisweagent.utils import error_taxonomy as et


class _Args(BaseModel):
    x: int = Field(description="an int")


def _boom(_args: BaseModel) -> str:
    raise RuntimeError("kaboom")


class TestClassifyException:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (subprocess.TimeoutExpired(cmd="x", timeout=1), et.ERROR_TIMEOUT),
            (PermissionError("denied"), et.ERROR_PERMISSION),
            (FileNotFoundError("nope"), et.ERROR_ENV_UNAVAILABLE),
            (OSError("disk hiccup"), et.ERROR_ENV_UNAVAILABLE),
            (ValueError("bad"), et.ERROR_TOOL_ERROR),
            (RuntimeError("boom"), et.ERROR_TOOL_ERROR),
        ],
    )
    def test_mapping(self, exc, expected):
        assert et.classify_exception(exc) == expected

    def test_permission_checked_before_oserror(self):
        """PermissionError is an OSError subclass; a wrong check order would swallow it."""
        assert et.classify_exception(PermissionError()) == et.ERROR_PERMISSION


class TestGuidance:
    @pytest.mark.parametrize("cls", [et.ERROR_TIMEOUT, et.ERROR_PERMISSION, et.ERROR_ENV_UNAVAILABLE])
    def test_permanent_classes_have_guidance(self, cls):
        text = et.guidance_for(cls)
        assert "Error class:" in text
        assert cls in text  # the class name itself is greppable in trajectories

    @pytest.mark.parametrize("cls", [et.ERROR_TOOL_ERROR, et.ERROR_INVALID_PARAMS, et.ERROR_UNKNOWN_TOOL])
    def test_other_classes_have_no_guidance(self, cls):
        assert et.guidance_for(cls) == ""


class TestRegistryErrorClass:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(ToolSpec(name="boom", description="", args_model=_Args, execute=_boom))

    def test_unknown_tool_classified(self):
        result = self.registry.execute_action({"tool": "nope", "args": {}})
        assert result["returncode"] == 1
        assert result["error_class"] == et.ERROR_UNKNOWN_TOOL

    def test_schema_failure_classified_invalid_params(self):
        result = self.registry.execute_action({"tool": "boom", "args": {"x": "abc"}})
        assert result["returncode"] == 1
        assert result["error_class"] == et.ERROR_INVALID_PARAMS

    def test_tool_exception_classified(self):
        result = self.registry.execute_action({"tool": "boom", "args": {"x": 1}})
        assert result["returncode"] == 1
        assert result["error_class"] == et.ERROR_TOOL_ERROR
        assert "kaboom" in result["output"]

    def test_oserror_tool_exception_classified_env_unavailable(self):
        """OSError-raising tool -> env_unavailable with model guidance in exception_info."""

        def _raise_not_found(_args):
            raise FileNotFoundError("missing.txt")

        registry = ToolRegistry()
        registry.register(ToolSpec(name="probe", description="", args_model=_Args, execute=_raise_not_found))
        result = registry.execute_action({"tool": "probe", "args": {"x": 1}})
        assert result["error_class"] == et.ERROR_ENV_UNAVAILABLE
        assert "Error class: env_unavailable" in result["exception_info"]

    def test_success_has_no_error_class_key(self):
        self.registry.register(ToolSpec(name="ok", description="", args_model=_Args, execute=lambda a: "fine"))
        result = self.registry.execute_action({"tool": "ok", "args": {"x": 1}})
        assert result["returncode"] == 0
        assert "error_class" not in result


class TestLocalEnvironmentErrorClass:
    def test_timeout_classified_with_guidance(self):
        env = LocalEnvironment(timeout=2)
        result = env.execute({"command": "sleep 30"})
        assert result["returncode"] == -1
        assert result["error_class"] == et.ERROR_TIMEOUT
        assert "Error class: timeout" in result["exception_info"]
        # the machine-readable tag also lands in extra (pre-existing hook, kept)
        assert result["extra"]["exception_type"] == "TimeoutExpired"

    def test_success_has_no_error_class_key(self):
        env = LocalEnvironment(timeout=10)
        result = env.execute({"command": "echo hi"})
        assert result["returncode"] == 0
        assert "error_class" not in result


class TestTransientRetry:
    """ToolSpec.transient_errors: declare-idempotent-or-no-retry (魔改 2 设计红线)."""

    def _registry_with_flaky(self, transient_errors, max_retries=2, fail_times=0):
        registry = ToolRegistry()
        counter = {"n": 0}

        def flaky(_args):
            counter["n"] += 1
            if counter["n"] <= fail_times:
                raise PermissionError("file locked")
            return "ok"

        registry.register(
            ToolSpec(
                name="flaky",
                description="",
                args_model=_Args,
                execute=flaky,
                transient_errors=transient_errors,
                max_retries=max_retries,
            )
        )
        return registry, counter

    def test_transient_error_retried_until_success(self, monkeypatch):
        monkeypatch.setattr(tr_module, "_sleep", lambda _s: None)
        registry, counter = self._registry_with_flaky((PermissionError,), fail_times=2)
        result = registry.execute_action({"tool": "flaky", "args": {"x": 1}})
        assert result["returncode"] == 0
        assert result["output"] == "ok"
        assert counter["n"] == 3  # 1 initial attempt + 2 retries

    def test_retry_budget_exhausted_notes_retries(self, monkeypatch):
        monkeypatch.setattr(tr_module, "_sleep", lambda _s: None)
        registry, counter = self._registry_with_flaky((PermissionError,), fail_times=99)
        result = registry.execute_action({"tool": "flaky", "args": {"x": 1}})
        assert result["returncode"] == 1
        assert result["error_class"] == et.ERROR_PERMISSION
        assert "auto-retried 2 time(s)" in result["output"]
        assert counter["n"] == 3  # budget respected, no runaway retrying

    def test_exponential_backoff_delays(self, monkeypatch):
        delays: list[float] = []
        monkeypatch.setattr(tr_module, "_sleep", delays.append)
        registry, _ = self._registry_with_flaky((PermissionError,), fail_times=99, max_retries=3)
        registry.execute_action({"tool": "flaky", "args": {"x": 1}})
        assert delays == [1, 2, 4]  # exponential, capped at 8s by design

    def test_non_transient_exception_never_retried(self, monkeypatch):
        monkeypatch.setattr(tr_module, "_sleep", lambda _s: None)
        registry, counter = self._registry_with_flaky((PermissionError,))

        def buggy(_args):
            counter["n"] += 1
            raise RuntimeError("bug")

        registry._tools["flaky"].execute = buggy
        result = registry.execute_action({"tool": "flaky", "args": {"x": 1}})
        assert result["returncode"] == 1
        assert result["error_class"] == et.ERROR_TOOL_ERROR
        assert counter["n"] == 1  # single attempt: undeclared errors get no framework retry

    def test_no_transient_errors_means_single_attempt(self, monkeypatch):
        monkeypatch.setattr(tr_module, "_sleep", lambda _s: None)
        registry, counter = self._registry_with_flaky((), fail_times=99)
        result = registry.execute_action({"tool": "flaky", "args": {"x": 1}})
        assert result["error_class"] == et.ERROR_PERMISSION
        assert counter["n"] == 1
        assert "auto-retried" not in result["output"]

    def test_builtin_read_tools_declare_permission_transient(self):
        for name in ("read_file", "grep", "list_dir"):
            spec = get_default_registry().get(name)
            assert spec.transient_errors == (PermissionError,), name

    def test_bash_has_no_retry_mechanism_at_all(self):
        """bash is environment-executed; the framework retry path cannot reach it."""
        spec = get_default_registry().get("bash")
        assert spec.execute is None
        assert spec.transient_errors == ()
