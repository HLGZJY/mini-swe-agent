"""令牌桶限流器（内部 API 网关用）。

契约：
- TokenBucket(capacity, rate, clock=time.monotonic)：
  capacity 桶容量（初始满桶），rate 令牌/秒，clock 可注入（测试传假时钟，
  返回浮点秒）。
- try_acquire(n=1) -> bool：桶内令牌 >= n 时扣减并返回 True，否则 False；
  **每次调用（无论成败）都先按 elapsed*rate 补充令牌**（不超过 capacity）。
- elapsed = clock() 与上次调用时刻之差；首次调用视为已充满（elapsed 不补）。
"""

from __future__ import annotations

import time


class TokenBucket:
    def __init__(self, capacity: float, rate: float, clock=time.monotonic):
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity and rate must be positive")
        self.capacity = float(capacity)
        self.rate = float(rate)
        self.clock = clock
        self.tokens = float(capacity)
        self._last = clock()

    def try_acquire(self, n: float = 1) -> bool:
        now = self.clock()
        elapsed = now - self._last
        self._last = now
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + self.rate)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
