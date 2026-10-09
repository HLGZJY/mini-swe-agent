"""基础行为测试（带时区输入主路径）。"""

from timestamps import to_utc


def test_z_suffix():
    assert to_utc("2026-03-01T08:00:00Z") == "2026-03-01T08:00:00Z"


def test_explicit_offset():
    assert to_utc("2026-03-01T08:00:00+08:00") == "2026-03-01T00:00:00Z"


def test_negative_offset():
    assert to_utc("2026-03-01T00:00:00-05:00") == "2026-03-01T05:00:00Z"


def test_output_format():
    out = to_utc("2026-12-31T23:59:59Z")
    assert out.endswith("Z") and len(out) == 20
