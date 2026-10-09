"""游标分页（内部工单列表接口用）。

契约：
- paginate(items, cursor, limit) -> (page, next_cursor)
- page = items[cursor : cursor+limit]（列表切片语义）
- next_cursor：后面还有元素时 = cursor + len(page)；没有更多时 = None
- 边界：剩余元素恰好 1 个时也必须给出 next_cursor（不能静默丢元素）
- cursor 越界（>= len(items)）时 page 为空、next_cursor 为 None
"""

from __future__ import annotations


def paginate(items: list, cursor: int = 0, limit: int = 5) -> tuple[list, int | None]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    page = list(items[cursor : cursor + limit])
    end = cursor + len(page)
    has_more = end < len(items) - 1
    next_cursor = end if has_more else None
    return page, next_cursor
