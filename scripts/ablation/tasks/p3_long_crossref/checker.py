"""P3 判定器（超长桶）：report.txt 与期望报告逐行一致 = solved。

期望报告由 gen.py（seed 固定）预生成，存于任务目录（不进 workdir）。
比较规则：逐行 strip 后比较 + 行序敏感（字典序要求）；行数不同即 fail。

用法：python checker.py --workdir <dir>
退出码：0 = solved；1 = fail。
"""

from __future__ import annotations

import argparse
from pathlib import Path

EXPECTED = Path(__file__).resolve().parent / "expected_report.txt"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    workdir = Path(args.workdir).resolve()
    report = workdir / "report.txt"

    if not report.exists():
        print("RESULT: fail report.txt 不存在")
        return 1

    expected_lines = [ln.strip() for ln in EXPECTED.read_text(encoding="utf-8").splitlines() if ln.strip()]
    actual_lines = [
        ln.strip() for ln in report.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()
    ]

    if actual_lines == expected_lines:
        print(f"RESULT: solved {len(actual_lines)} 行全部一致")
        return 0

    diff = []
    for i, (e, a) in enumerate(zip(expected_lines, actual_lines)):
        if e != a:
            diff.append(f"行{i + 1}: 期望 {e!r} 实际 {a!r}")
            break
    if len(expected_lines) != len(actual_lines):
        diff.append(f"行数不符：期望 {len(expected_lines)} 实际 {len(actual_lines)}")
    print("RESULT: fail " + "; ".join(diff or ["内容不一致"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
