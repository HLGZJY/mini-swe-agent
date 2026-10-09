"""基础行为测试（主路径）。"""

import pytest
from phones import normalize_phone


def test_plain():
    assert normalize_phone("13812345678") == "13812345678"


def test_separators():
    assert normalize_phone("138-1234-5678") == "13812345678"
    assert normalize_phone("138 1234 5678") == "13812345678"


def test_plus86():
    assert normalize_phone("+8613812345678") == "13812345678"


def test_invalid_raises():
    with pytest.raises(ValueError):
        normalize_phone("12345")
