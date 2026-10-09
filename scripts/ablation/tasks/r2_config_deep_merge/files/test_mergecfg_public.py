"""基础行为测试（两层合并主路径）。"""

from mergecfg import deep_merge


def test_top_level_override():
    base = {"a": 1, "b": 2}
    assert deep_merge(base, {"b": 3}) == {"a": 1, "b": 3}
    assert deep_merge(base, {"c": 9})["c"] == 9


def test_two_level_dict_merge():
    base = {"log": {"level": "info", "file": "a.log"}}
    out = deep_merge(base, {"log": {"level": "debug"}})
    assert out == {"log": {"level": "debug", "file": "a.log"}}


def test_scalar_overrides_dict():
    base = {"log": {"level": "info"}}
    assert deep_merge(base, {"log": None}) == {"log": None}


def test_input_not_mutated():
    base = {"a": {"x": 1}}
    deep_merge(base, {"a": {"y": 2}})
    assert base == {"a": {"x": 1}}
