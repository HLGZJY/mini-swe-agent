"""基础行为测试（分页主路径）。"""

from pager import paginate


def test_basic_slice():
    page, nxt = paginate(list(range(5)), 0, 3)
    assert page == [0, 1, 2]
    assert nxt == 3


def test_last_page():
    page, nxt = paginate(list(range(5)), 3, 3)
    assert page == [3, 4]
    assert nxt is None


def test_exact_fit():
    page, nxt = paginate(list(range(6)), 0, 3)
    assert page == [0, 1, 2]
    assert nxt == 3
    page2, nxt2 = paginate(list(range(6)), 3, 3)
    assert page2 == [3, 4, 5]
    assert nxt2 is None


def test_cursor_beyond_end():
    page, nxt = paginate([1, 2], 10, 3)
    assert page == []
    assert nxt is None
