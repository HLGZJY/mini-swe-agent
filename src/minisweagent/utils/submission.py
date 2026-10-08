"""Shared helpers for detecting the task-submission marker in command output.

Every environment implements its own ``_check_finished`` so it can raise :class:`Submitted`
as soon as the agent signals completion.  The logic is identical everywhere, so it lives
here instead of being copy-pasted into each environment module.
"""

from minisweagent.exceptions import Submitted

SUBMISSION_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

_QUOTES = ("'", '"')


def _strip_quotes(text: str) -> str:
    """Strip a single matching pair of surrounding quotes.

    ``cmd.exe`` does not strip single quotes (only double ones), so
    ``echo 'MARKER'`` emits the quotes verbatim.  Without this the completion signal
    is never recognised on Windows and the agent runs until it hits its limits.
    """
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in _QUOTES:
        return text[1:-1].strip()
    return text


def check_finished(output: dict) -> None:
    """Raise :class:`Submitted` if ``output`` shows the agent submitted its work.

    The marker must be on the first line of output and the command must have succeeded.
    Everything after the marker line is collected as the submission payload.
    """
    lines = output.get("output", "").lstrip().splitlines(keepends=True)
    if not lines or output.get("returncode") != 0:
        return
    if _strip_quotes(lines[0].lstrip()) != SUBMISSION_MARKER:
        return
    submission = "".join(lines[1:])
    raise Submitted(
        {
            "role": "exit",
            "content": submission,
            "extra": {"exit_status": "Submitted", "submission": submission},
        }
    )
