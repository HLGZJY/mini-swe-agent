"""隐藏判定：report.txt 与期望报告逐行一致。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import workdir_from_argv  # noqa: E402


def main() -> None:
    workdir = workdir_from_argv()
    expected = (Path(__file__).resolve().parent / "expected_report.txt").read_text(encoding="utf-8")
    report_path = workdir / "report.txt"
    if not report_path.exists():
        print("RESULT: fail (report.txt missing)")
        raise SystemExit(1)
    got = report_path.read_text(encoding="utf-8", errors="replace")
    exp_lines = [line.strip() for line in expected.splitlines() if line.strip()]
    got_lines = [line.strip() for line in got.splitlines() if line.strip()]
    if got_lines == exp_lines:
        print(f"RESULT: solved {len(got_lines)} lines match")
        raise SystemExit(0)
    print(f"RESULT: fail (mismatch: expected {exp_lines[:3]}... got {got_lines[:3]}...)")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
