"""Tests for shell resolution in LocalEnvironment."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from minisweagent.environments.local import (
    LocalEnvironment,
    LocalEnvironmentConfig,
    _resolve_shell,
)

BASH = __import__("shutil").which("bash")


class TestConfig:
    def test_default_shell_is_auto(self):
        """Default must stay 'auto' so existing configs are unaffected."""
        assert LocalEnvironmentConfig().shell == "auto"

    def test_original_fields_unchanged(self):
        config = LocalEnvironmentConfig()
        assert config.cwd == ""
        assert config.env == {}
        assert config.timeout == 30

    def test_rejects_unknown_shell(self):
        # pydantic validates the Literal before _resolve_shell is ever reached.
        with pytest.raises(ValidationError):
            LocalEnvironmentConfig(shell="fish")


class TestResolveShell:
    def test_rejects_unknown_shell(self):
        with pytest.raises(ValueError, match="Unknown shell"):
            _resolve_shell("fish")

    def test_bash_returns_argv_form(self):
        """bash must be invoked as [bash, -c, cmd], not via shell=True."""
        kind, exe = _resolve_shell("bash")
        if BASH is None:
            pytest.skip("no bash on PATH")
        assert kind == "bash"
        assert exe == BASH

    def test_cmd_pins_comspec(self):
        kind, exe = _resolve_shell("cmd")
        assert kind == "default"
        assert "cmd" in exe.lower()

    def test_auto_prefers_bash_when_available(self):
        kind, _ = _resolve_shell("auto")
        if BASH is None:
            assert kind == "default"
        else:
            assert kind == "bash"


class TestShellBehaviour:
    def test_serialize_records_resolved_shell(self):
        """The resolved shell must be visible in the trajectory, otherwise you cannot
        tell which interpreter burned your commands."""
        env = LocalEnvironment()
        shell_info = env.serialize()["info"]["shell"]
        assert shell_info["requested"] == "auto"
        assert "resolved" in shell_info

    def test_bash_interprets_posix_constructs(self):
        """The whole point: on Windows these fail under cmd.exe but work under bash."""
        env = LocalEnvironment(shell="bash")
        results = {
            "var": env.execute({"command": "echo $HOME"})["output"].strip(),
            "escape": env.execute({"command": r"echo -e 'a\nb'"})["output"],
            "multiline": env.execute({"command": "echo one\necho two"})["output"],
            "pipe": env.execute({"command": "echo hi | tr a-z A-Z"})["output"].strip(),
        }
        assert results["var"] and "$HOME" not in results["var"]
        assert results["escape"] == "a\nb\n"
        assert results["multiline"] == "one\ntwo\n"
        assert results["pipe"] == "HI"

    def test_cwd_is_honoured(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            env = LocalEnvironment(cwd=temp_dir, shell="bash")
            assert env.execute({"command": "pwd"})["returncode"] == 0
            # bash prints MSYS-style paths, so compare on the basename only.
            assert Path(env.execute({"command": "pwd"})["output"].strip()).name == Path(temp_dir).name

    def test_env_vars_are_passed_through(self):
        env = LocalEnvironment(env={"MSWEA_TEST_VAR": "from_config"}, shell="bash")
        assert env.execute({"command": "echo $MSWEA_TEST_VAR"})["output"].strip() == "from_config"

    def test_timeout_is_reported_as_error_result(self):
        """A timeout must surface as returncode -1, not as a raised exception:
        execute() deliberately converts exceptions into an error dict."""
        env = LocalEnvironment(timeout=1, shell="bash")
        result = env.execute({"command": f'"{sys.executable}" -c "import time; time.sleep(30)"'})
        assert result["returncode"] == -1
        assert result["exception_info"]

    def test_default_shell_uses_shell_true_semantics(self):
        """With shell='cmd' the command must go through cmd.exe, not bash."""
        env = LocalEnvironment(shell="cmd")
        # Under cmd.exe, $HOME is not expanded.
        assert env.execute({"command": "echo $HOME"})["output"].strip() == "$HOME"
