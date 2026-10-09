"""隐藏判定：get 必须刷新新旧序（LRU 语义核心）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _checkerlib import main  # noqa: E402


def get_refreshes_recency(mods):
    lru = mods["lru"]
    c = lru.LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # a 变最新
    c.put("c", 3)  # 淘汰 b，不是 a
    assert c.get("a") == 1, "get 命中必须刷新新旧序（a 不该被淘汰）"
    assert c.get("b") is None


def put_refreshes_recency(mods):
    lru = mods["lru"]
    c = lru.LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 9)  # 覆盖也算访问
    c.put("c", 3)
    assert c.get("a") == 9
    assert c.get("b") is None


def capacity_one(mods):
    lru = mods["lru"]
    c = lru.LRUCache(1)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") is None and c.get("b") == 2


def miss_does_not_evict(mods):
    lru = mods["lru"]
    c = lru.LRUCache(2)
    c.put("a", 1)
    c.get("zzz")  # 未命中不得影响新旧序
    c.put("b", 2)
    c.put("c", 3)
    assert c.get("a") is None and c.get("b") == 2 and c.get("c") == 3


if __name__ == "__main__":
    main(
        [
            ("get_refreshes_recency", get_refreshes_recency),
            ("put_refreshes_recency", put_refreshes_recency),
            ("capacity_one", capacity_one),
            ("miss_does_not_evict", miss_does_not_evict),
        ]
    )
