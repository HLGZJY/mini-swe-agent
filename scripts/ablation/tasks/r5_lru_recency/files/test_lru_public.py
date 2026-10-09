"""基础行为测试（容量淘汰主路径）。"""

from lru import LRUCache


def test_basic_get_put():
    c = LRUCache(2)
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None


def test_evict_oldest_inserted():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_overwrite_value():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("a", 9)
    assert c.get("a") == 9
    c.put("b", 2)
    c.put("c", 3)
    assert c.get("a") is None
