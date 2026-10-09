"""隐藏判定：三层叠加（环境层与实例层改同一二级块的不同键）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def three_layer_stack(mods):
    mergecfg = mods["mergecfg"]
    base = {"svc": {"retry": {"times": 3, "delay": 0.5}, "timeout": 10}}
    env = {"svc": {"retry": {"delay": 1.0}}}
    inst = {"svc": {"retry": {"times": 5}}}
    out = mergecfg.deep_merge(mergecfg.deep_merge(base, env), inst)
    assert out == {
        "svc": {"retry": {"times": 5, "delay": 1.0}, "timeout": 10}
    }, f"third layer lost sibling keys: {out}"


def deep_nesting(mods):
    mergecfg = mods["mergecfg"]
    base = {"a": {"b": {"c": 1, "d": 2}}}
    out = mergecfg.deep_merge(base, {"a": {"b": {"c": 9}}})
    assert out == {"a": {"b": {"c": 9, "d": 2}}}, f"depth-3 keys lost: {out}"


def list_whole_override(mods):
    mergecfg = mods["mergecfg"]
    base = {"tags": [1, 2], "x": {"y": [1]}}
    out = mergecfg.deep_merge(base, {"tags": [3]})
    assert out["tags"] == [3], "list must be replaced whole, not merged"
    out2 = mergecfg.deep_merge(base, {"x": {"y": [9]}})
    assert out2 == {"tags": [1, 2], "x": {"y": [9]}}


def fresh_keys_added(mods):
    mergecfg = mods["mergecfg"]
    out = mergecfg.deep_merge({"a": {"x": 1}}, {"b": {"z": 2}})
    assert out == {"a": {"x": 1}, "b": {"z": 2}}


if __name__ == "__main__":
    main(
        [
            ("three_layer_stack", three_layer_stack),
            ("deep_nesting", deep_nesting),
            ("list_whole_override", list_whole_override),
            ("fresh_keys_added", fresh_keys_added),
        ]
    )
