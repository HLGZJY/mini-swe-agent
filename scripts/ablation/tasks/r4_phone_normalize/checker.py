"""隐藏判定：前缀组合与非法输入全谱。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def ok(mods, raw, want):
    got = mods["phones"].normalize_phone(raw)
    assert got == want, f"{raw!r} -> {got!r}, want {want!r}"


def bad(mods, raw):
    try:
        mods["phones"].normalize_phone(raw)
    except ValueError as exc:
        if "invalid phone" not in str(exc):
            raise AssertionError(f"wrong message: {exc}") from exc
    else:
        raise AssertionError(f"{raw!r} must raise ValueError")


def t_all_forms(mods):
    ok(mods, "+86 138-1234-5678", "13812345678")
    ok(mods, "8613812345678", "13812345678")
    ok(mods, "+8613812345678", "13812345678")
    ok(mods, "(138) 1234-5678", "13812345678")
    ok(mods, "138.1234.5678", "13812345678")
    ok(mods, "  13812345678  ", "13812345678")


def t_invalid(mods):
    bad(mods, "1381234567")  # 10 位
    bad(mods, "138123456789")  # 12 位
    bad(mods, "23812345678")  # 非 1 开头
    bad(mods, "")  # 空
    bad(mods, "abcdefghijk")  # 非数字
    bad(mods, "+86-138123456789")  # 前缀剥离后仍不合法


def t_prefix_edge(mods):
    # 86 前缀剥离只对 13 位生效：12 位 86 开头不能剥成 10 位再误判
    bad(mods, "861381234567")
    # 0 前缀是座机语境，契约未授权剥 0，必须拒绝
    bad(mods, "013812345678")


if __name__ == "__main__":
    main(
        [
            ("t_all_forms", t_all_forms),
            ("t_invalid", t_invalid),
            ("t_prefix_edge", t_prefix_edge),
        ]
    )
