"""隐藏判定：全边界扫描（公开测试覆盖不到的 has_more 边界）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def sweep(mods):
    pager = mods["pager"]
    items = list(range(7))
    # 全 (cursor, limit) 组合下逐页翻完，收集元素 —— 不许丢也不许重
    for limit in (1, 2, 3, 5, 7):
        got, cursor, guard = [], 0, 0
        while cursor is not None:
            page, cursor = pager.paginate(items, cursor, limit)
            got.extend(page)
            guard += 1
            assert guard < 20, "possible infinite pagination loop"
        assert got == items, f"limit={limit}: lost/dup elements: {got}"


def one_remaining(mods):
    pager = mods["pager"]
    # 剩余恰好 1 个：page1 取 3 个后还剩 1 个，next_cursor 必须非 None
    page, nxt = pager.paginate([10, 20, 30, 40], 0, 3)
    assert page == [10, 20, 30] and nxt == 3, f"one-remaining broken: nxt={nxt}"
    page2, nxt2 = pager.paginate([10, 20, 30, 40], 3, 3)
    assert page2 == [40] and nxt2 is None


def negative_limit(mods):
    pager = mods["pager"]
    try:
        pager.paginate([1], 0, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("limit=0 must raise ValueError")


def empty_input(mods):
    pager = mods["pager"]
    page, nxt = pager.paginate([], 0, 3)
    assert page == [] and nxt is None


if __name__ == "__main__":
    main(
        [
            ("sweep", sweep),
            ("one_remaining", one_remaining),
            ("negative_limit", negative_limit),
            ("empty_input", empty_input),
        ]
    )
