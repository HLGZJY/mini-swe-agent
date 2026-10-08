import os
import platform
import shutil
import signal
import subprocess
from typing import Any, Literal

from pydantic import BaseModel

from minisweagent.utils.log import logger
from minisweagent.utils.serialize import recursive_merge
from minisweagent.utils.submission import check_finished

_SHELLS = ("auto", "bash", "cmd", "powershell")


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30
    shell: Literal["auto", "bash", "cmd", "powershell"] = "auto"
    """Which shell interprets the commands. ``auto`` prefers a real bash (Git Bash/WSL)
    and falls back to the platform's default shell. Set this explicitly if ``auto``
    picks the wrong bash (e.g. a WSL one that has no distro mounted)."""


def _resolve_shell(spec: str) -> tuple[str, str]:
    """Return ``(kind, executable)`` for the requested shell.

    ``kind`` is ``"bash"`` when the command must be passed as ``[bash, "-c", cmd]``,
    otherwise ``"default"`` for ``subprocess``'s own ``shell=True`` handling.
    """
    if spec not in _SHELLS:
        raise ValueError(f"Unknown shell {spec!r}, available: {_SHELLS}")
    if spec == "auto":
        bash = shutil.which("bash")
        if bash:
            # A WSL bash lives in System32 and needs a mounted distro; probe it.
            if _bash_works(bash):
                return "bash", bash
            logger.warning(f"Found bash at '{bash}' but it is not usable, falling back to the default shell.")
        return "default", ""
    if spec == "bash":
        bash = shutil.which("bash")
        if not bash:
            raise RuntimeError("shell='bash' was requested but no bash executable was found on PATH")
        return "bash", bash
    if spec == "cmd":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return "default", comspec
    return "default", shutil.which("powershell") or shutil.which("pwsh") or ""


def _bash_works(bash: str) -> bool:
    """Check that ``bash`` actually runs, not just that it exists on PATH."""
    try:
        result = subprocess.run([bash, "-c", "echo ok"], capture_output=True, text=True, timeout=10, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "ok" in (result.stdout or "")


class LocalEnvironment:
    def __init__(self, *, config_class: type = LocalEnvironmentConfig, **kwargs):
        """This class executes shell commands directly on the local machine.

        By default (``shell="auto"``) commands are handed to a real bash when one is
        available, because the agent's prompts assume POSIX shell semantics. On Windows
        that means Git Bash rather than ``cmd.exe``.
        """
        self.config = config_class(**kwargs)
        self._shell_kind, self._shell_exe = _resolve_shell(self.config.shell)
        if self._shell_kind == "bash":
            logger.info(f"LocalEnvironment is using bash: {self._shell_exe}")
        else:
            logger.info("LocalEnvironment is using the platform's default shell")

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command in the local environment and return the result as a dict."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        try:
            result = _run(
                command,
                cwd,
                os.environ | self.config.env,
                timeout or self.config.timeout,
                shell_kind=self._shell_kind,
                shell_exe=self._shell_exe,
            )
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion."""
        check_finished(output)

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(self.config.model_dump(), platform.uname()._asdict(), os.environ, kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
                "shell": {"requested": self.config.shell, "resolved": self._shell_exe or "default"},
            }
        }


def _run(
    command: str,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    *,
    shell_kind: str = "default",
    shell_exe: str = "",
) -> subprocess.CompletedProcess[str]:
    """Like subprocess.run, but kills the whole process group on timeout so no children are orphaned.

    With ``shell_kind="bash"`` the command is passed as ``[bash, "-c", command]`` instead of
    relying on ``shell=True``. That keeps POSIX semantics intact on platforms where
    ``shell=True`` would hand the command to ``cmd.exe``.
    """
    popen_kwargs: dict[str, Any] = {}
    if shell_kind == "bash":
        popen_kwargs["executable"] = shell_exe
        command = [shell_exe, "-c", command]
    else:
        popen_kwargs["shell"] = True
        if shell_exe:
            # Pin the interpreter, otherwise Windows silently uses COMSPEC.
            popen_kwargs["executable"] = shell_exe
    process = subprocess.Popen(
        command,
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
        **popen_kwargs,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout)
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
