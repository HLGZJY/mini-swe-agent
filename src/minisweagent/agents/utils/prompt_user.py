from prompt_toolkit.formatted_text.html import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

from minisweagent import global_config_dir

_history = FileHistory(global_config_dir / "interactive_history.txt")


class _LazyPromptSession:
    """A lazy proxy around :class:`PromptSession`.

    ``PromptSession`` binds to the real console output at construction time
    (on Windows it raises ``NoConsoleScreenBufferError`` when no console
    handle is available). Constructing sessions at module import time made
    every test module that (transitively) imports this file uncollectable in
    non-interactive environments (CI, pipes, sandboxes).

    The proxy defers the real construction until ``.prompt()`` (or any other
    session attribute) is first used. Tests that patch
    ``prompt_session.prompt`` work directly on the proxy instance and never
    trigger the underlying construction.
    """

    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs
        self._session: PromptSession | None = None

    def _get_session(self) -> PromptSession:
        if self._session is None:
            self._session = PromptSession(**self._kwargs)
        return self._session

    def prompt(self, *args, **kwargs):
        return self._get_session().prompt(*args, **kwargs)

    def __getattr__(self, name: str):
        # Only reached for attributes the proxy itself does not define.
        return getattr(self._get_session(), name)


prompt_session = _LazyPromptSession(history=_history)
_multiline_prompt_session = _LazyPromptSession(history=_history, multiline=True)


def _multiline_prompt() -> str:
    return _multiline_prompt_session.prompt(
        "",
        bottom_toolbar=HTML(
            "Submit message: <b fg='yellow' bg='black'>Esc, then Enter</b> | "
            "Navigate history: <b fg='yellow' bg='black'>Arrow Up/Down</b> | "
            "Search history: <b fg='yellow' bg='black'>Ctrl+R</b>"
        ),
    )
