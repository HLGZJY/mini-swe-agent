"""日志时间戳归一化（多区部署的日志统一入库用）。

契约：
- to_utc(ts: str) -> str，统一输出 "%Y-%m-%dT%H:%M:%SZ"（UTC）
- 输入是 ISO 8601 字符串：
  * 带时区偏移（如 +08:00 / Z）→ 正确换算到 UTC；
  * 不带时区（naive）→ 按业务约定视为**东八区**（采集端全部部署在 +08:00），
    先补东八区时区再换算 UTC。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def to_utc(ts: str) -> str:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
