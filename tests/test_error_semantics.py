"""魔改 2 · 执行失败错误分类：taxonomy 映射 + 两条执行路径的观察打标（error_class）。

约定：观察 dict 的 ``error_class`` 键**只在失败时存在**（「缺 = 成功」），
模型可读的分类与引导文案拼在 ``exception_info`` 里（<exception> 渲染通道）。
"""

import subprocess

import pytest
from pydantic import BaseModel, Field

from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.utils.tool_registry import ToolRegistry, ToolSpec
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
