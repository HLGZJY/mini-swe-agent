"""基础行为测试（单次获取主路径）。"""

from ratelimit import TokenBucket


def test_initial_full():
    tb = TokenBucket(3, 1.0, clock=lambda: 0.0)
    assert tb.try_acquire() is True


def test_exhaustion():
    clock = {"t": 0.0}
    tb = TokenBucket(2, 1.0, clock=lambda: clock["t"])
    assert tb.try_acquire() is True
    assert tb.try_acquire() is True
    assert tb.try_acquire() is False


def test_invalid_args():
    try:
        TokenBucket(0, 1.0)
    except ValueError:
        return
    raise AssertionError("capacity=0 must raise")
