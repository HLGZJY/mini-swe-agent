"""基础行为测试（纯函数部分，无网络）。"""

from fxsync import convert


def test_convert():
    assert convert(100.0, {"USD->CNY": 7.2}) == 720.0


def test_convert_zero():
    assert convert(0.0, {"USD->CNY": 7.2}) == 0.0
