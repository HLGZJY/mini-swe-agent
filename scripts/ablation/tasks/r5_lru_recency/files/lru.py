"""容量受限的会话缓存（LRU 语义）。

契约：
- LRUCache(capacity)：capacity >= 1；超容量时淘汰「最久未被访问」的键；
- get(key)：命中返回值并把该键刷成最新；未命中返回 None；
- put(key, value)：写入并把该键刷成最新；
- 「访问」包括 get 命中与 put。
"""

from __future__ import annotations

from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._data: OrderedDict = OrderedDict()

    def get(self, key):
        if key not in self._data:
            return None
        return self._data[key]

    def put(self, key, value) -> None:
        if key in self._data:
            self._data[key] = value
            return
        if len(self._data) >= self.capacity:
            self._data.popitem(last=False)
        self._data[key] = value
