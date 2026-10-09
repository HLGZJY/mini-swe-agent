"""隐藏判定：naive 东八区规则 + 跨天边界。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def naive_is_cst(mods):
    ts = mods["timestamps"]
    # naive 输入按东八区解释：08:00 +08:00 == 00:00 UTC
    assert ts.to_utc("2026-03-01T08:00:00") == "2026-03-01T00:00:00Z", ts.to_utc(
        "2026-03-01T08:00:00"
    )


def naive_cross_day(mods):
    ts = mods["timestamps"]
    # 东八区 07:00 换 UTC 是前一天 23:00
    assert ts.to_utc("2026-03-01T07:00:00") == "2026-02-28T23:00:00Z"
    # 东八区 16:30 同日
    assert ts.to_utc("2026-03-01T16:30:00") == "2026-03-01T08:30:00Z"


def aware_still_works(mods):
    ts = mods["timestamps"]
    assert ts.to_utc("2026-03-01T08:00:00+08:00") == "2026-03-01T00:00:00Z"
    assert ts.to_utc("2026-03-01T08:00:00Z") == "2026-03-01T08:00:00Z"
    assert ts.to_utc("2026-03-01T00:00:00-05:00") == "2026-03-01T05:00:00Z"


def fractional_seconds(mods):
    ts = mods["timestamps"]
    # 带小数秒的 naive：仍按东八区，输出截断到秒
    assert ts.to_utc("2026-03-01T08:00:00.500") == "2026-03-01T00:00:00Z"


if __name__ == "__main__":
    main(
        [
            ("naive_is_cst", naive_is_cst),
            ("naive_cross_day", naive_cross_day),
            ("aware_still_works", aware_still_works),
            ("fractional_seconds", fractional_seconds),
        ]
    )
