"""告警指纹（G1 定案）：canonical 稳定 label 子集 + 时间窗桶 → sha256 hex。

契约来源：docs/design/m1-alert-ingestion-design.md G1 + 评审依据 R2
（prometheus/common `SignatureWithoutLabels`：稳定 label 子集 + 排序 + 0xFF 分隔）、
CONTEXT.md「告警指纹 = 由规则+实例+时间窗算出的稳定哈希」。

**为什么哈希输入含时间窗桶**：`alert_events.fingerprint` 带唯一约束（D-13 的去重
锚点）。若指纹只由 label 子集算出，同一告警任何时刻都只能有一行，「超窗新行」与
`dedup_window` 就无从表达。把时间窗桶并入哈希后：窗内同指纹 → 行内合并；跨桶 →
指纹不同 → 新行。唯一约束、超窗新行、窗口可配三者同时成立（决策：decisions.md D-14）。
代价是桶边界附近（如 9:59 与 10:01）会分属两行——保守方向，不会漏收。

哈希输入只吃稳定字段：canonical label 子集 `{alertname, instance, job}`（存在者参与，
按字典序固定）+ 时间窗桶，以 0xFF 分隔拼接；数值 / annotations / generatorURL /
AM 自带 fingerprint（全 labels FNV-1a，含易变 label）一律不入哈希，只作溯源。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

DEFAULT_DEDUP_WINDOW = timedelta(minutes=10)
CANONICAL_LABELS: tuple[str, ...] = ("alertname", "instance", "job")  # 字典序，哈希输入序固定
SEPARATOR = b"\xff"  # prometheus/common SeparatorByte：防 `ab`+`c` 与 `a`+`bc` 拼接碰撞


def as_utc(moment: datetime) -> datetime:
    """无时区信息的时间戳按 UTC 处理（SQLite 取回的 datetime 无 tzinfo）。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def window_bucket(fired_at: datetime, window: timedelta = DEFAULT_DEDUP_WINDOW) -> int:
    """时间窗桶序号 = floor(epoch 秒 / 窗口秒数)。"""
    window_seconds = max(int(window.total_seconds()), 1)
    return int(as_utc(fired_at).timestamp()) // window_seconds


def canonical_fingerprint(
    labels: Mapping[str, str],
    fired_at: datetime,
    window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> str:
    """主指纹：canonical label 子集 + 时间窗桶 → sha256 hex（同告警重发稳定）。"""
    return _hash(labels, window_bucket(fired_at, window))


def candidate_fingerprints(
    labels: Mapping[str, str],
    fired_at: datetime,
    window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> tuple[str, str]:
    """合并候选指纹 =（当前桶，前一桶）。

    桶宽 = 窗宽，故任一与 fired_at 相距 ≤ 窗宽的时间点只可能落在当前桶或前一桶；
    查这两个桶即可覆盖滑窗语义（跨桶但仍在窗内 → 合并进原行）。
    """
    bucket = window_bucket(fired_at, window)
    return (_hash(labels, bucket), _hash(labels, bucket - 1))


def _hash(labels: Mapping[str, str], bucket: int) -> str:
    # 2026-09-30 评审后「简化」：字段拼接天然无歧义，逐段塞 0xFF 冗余，去掉。
    parts = [name.encode("utf-8") + labels[name].encode("utf-8") for name in CANONICAL_LABELS if labels.get(name)]
    parts.append(str(bucket).encode("ascii"))
    return hashlib.sha256(b"".join(parts)).hexdigest()
