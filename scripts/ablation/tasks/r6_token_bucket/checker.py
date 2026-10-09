"""隐藏判定：注入假时钟的连续时序（补充量必须与 elapsed 成比例）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def burst_then_recover(mods):
    ratelimit = mods["ratelimit"]
    clock = {"t": 0.0}
    tb = ratelimit.TokenBucket(3, 2.0, clock=lambda: clock["t"])
    assert tb.try_acquire() is True  # 3 -> 2
    assert tb.try_acquire() is True  # 2 -> 1
    assert tb.try_acquire() is True  # 1 -> 0
    assert tb.try_acquire() is False  # 空
    clock["t"] = 1.0  # 空闲 1 秒：+2 令牌
    assert tb.try_acquire() is True, "elapsed*rate=2 个令牌必须放行 1 个"
    assert tb.try_acquire() is True, "剩余 1 个令牌应放行"


def proportional_refill(mods):
    ratelimit = mods["ratelimit"]
    clock = {"t": 0.0}
    tb = ratelimit.TokenBucket(2, 1.0, clock=lambda: clock["t"])
    assert tb.try_acquire() is True  # 2 -> 1
    assert tb.try_acquire() is True  # 1 -> 0（第 2 个令牌本来就在桶里）
    clock["t"] = 0.5  # +0.5 个令牌 < 1：不放行
    assert tb.try_acquire() is False, "半秒只补 0.5 个，不够 1 个"
    clock["t"] = 1.0  # 再 +0.5，累计 1.0：刚好放行
    assert tb.try_acquire() is True


def failed_call_still_refills_and_advances(mods):
    ratelimit = mods["ratelimit"]
    clock = {"t": 0.0}
    tb = ratelimit.TokenBucket(1, 10.0, clock=lambda: clock["t"])
    assert tb.try_acquire() is True  # 空
    clock["t"] = 5.0
    assert tb.try_acquire(3) is False, "capacity=1 攒不出 3 个"
    # 失败调用也推进了时钟基线：0.5 秒后再试只补 0.5*10=5→cap 1 → 可放行 1 个
    clock["t"] = 5.5
    assert tb.try_acquire() is True


def no_negative_time(mods):
    # 时钟回拨（elapsed<=0）不得给令牌也不得崩
    ratelimit = mods["ratelimit"]
    clock = {"t": 10.0}
    tb = ratelimit.TokenBucket(1, 5.0, clock=lambda: clock["t"])
    assert tb.try_acquire() is True
    clock["t"] = 9.0
    assert tb.try_acquire() is False


if __name__ == "__main__":
    main(
        [
            ("burst_then_recover", burst_then_recover),
            ("proportional_refill", proportional_refill),
            ("failed_call_still_refills_and_advances", failed_call_still_refills_and_advances),
            ("no_negative_time", no_negative_time),
        ]
    )
