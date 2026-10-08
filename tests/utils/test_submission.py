"""Tests for the shared submission-marker detection."""

import pytest

from minisweagent.exceptions import Submitted
from minisweagent.utils.submission import SUBMISSION_MARKER, _strip_quotes, check_finished


class TestStripQuotes:
    def test_strips_single_quotes(self):
        """cmd.exe keeps single quotes verbatim, so the marker arrives quoted."""
        assert _strip_quotes(f"'{SUBMISSION_MARKER}'") == SUBMISSION_MARKER

    def test_strips_double_quotes(self):
        assert _strip_quotes(f'"{SUBMISSION_MARKER}"') == SUBMISSION_MARKER

    def test_strips_quotes_with_inner_whitespace(self):
        assert _strip_quotes(f"'  {SUBMISSION_MARKER}  '") == SUBMISSION_MARKER

    def test_leaves_bare_marker_untouched(self):
        assert _strip_quotes(SUBMISSION_MARKER) == SUBMISSION_MARKER

    def test_leaves_mismatched_quotes_untouched(self):
        assert _strip_quotes(f"'{SUBMISSION_MARKER}\"") == f"'{SUBMISSION_MARKER}\""

    def test_leaves_single_char_untouched(self):
        """A lone quote character must not be treated as a quoted string."""
        assert _strip_quotes("'") == "'"

    def test_leaves_inner_quotes_untouched(self):
        """Only the outer pair is stripped, not nested ones."""
        assert _strip_quotes("''a''") == "'a'"


class TestCheckFinished:
    def test_raises_on_bare_marker(self):
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f"{SUBMISSION_MARKER}\npayload\n", "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == "payload\n"

    def test_raises_on_single_quoted_marker(
        self,
    ):
        """Regression test: this is the case that never worked on Windows."""
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f"'{SUBMISSION_MARKER}'\npayload\n", "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == "payload\n"

    def test_raises_on_double_quoted_marker(self):
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f'"{SUBMISSION_MARKER}"\npayload\n', "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == "payload\n"

    def test_raises_on_crlf_output(self):
        """cmd.exe emits \\r\\n; the marker line must still be recognised."""
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f"{SUBMISSION_MARKER}\r\npayload\r\n", "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == "payload\r\n"

    def test_raises_when_marker_preceded_by_blank_lines(self):
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f"\n\n{SUBMISSION_MARKER}\npayload\n", "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == "payload\n"

    def test_does_not_raise_without_marker(self):
        check_finished({"output": "all good\n", "returncode": 0})

    def test_does_not_raise_on_empty_output(self):
        check_finished({"output": "", "returncode": 0})

    def test_does_not_raise_on_nonzero_returncode(self):
        """A failing command must never be mistaken for a submission."""
        check_finished({"output": f"{SUBMISSION_MARKER}\npayload\n", "returncode": 1})

    def test_does_not_raise_when_marker_is_not_first_line(self):
        check_finished({"output": f"noise\n{SUBMISSION_MARKER}\npayload\n", "returncode": 0})

    def test_does_not_raise_when_marker_only_appears_inline(self):
        check_finished({"output": f"echo {SUBMISSION_MARKER} was here\n", "returncode": 0})

    def test_submission_is_empty_when_marker_is_only_line(self):
        with pytest.raises(Submitted) as exc:
            check_finished({"output": SUBMISSION_MARKER, "returncode": 0})
        assert exc.value.messages[0]["extra"]["submission"] == ""

    def test_exit_status_is_submitted(self):
        with pytest.raises(Submitted) as exc:
            check_finished({"output": f"{SUBMISSION_MARKER}\n", "returncode": 0})
        assert exc.value.messages[0]["extra"]["exit_status"] == "Submitted"
