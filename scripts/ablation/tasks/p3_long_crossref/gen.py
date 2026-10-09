"""P3 数据生成器：确定性（seed 固定），生成 services.json + 6 天日志 + 期望报告。

用法（在任务目录下）：python gen.py
产物：files/services.json、files/logs/app-2026-01-0{1..6}.log、expected_report.txt

日志行格式：`2026-01-0D HH:MM:SS LEVEL [ref] code=EXXX message`
- ref = svc_id 或任一别名（混用，强制别名归并）
- E41 前缀的 ERROR 行是统计对象；E40/E50 是干扰项
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

SEED = 20261009
N_SERVICES = 40
N_DAYS = 8
LINES_PER_LOG = 700

SERVICE_NAMES = [
    "billing-api",
    "auth-svc",
    "gateway",
    "notify-worker",
    "search-index",
    "user-profile",
    "order-sync",
    "payment-callback",
    "sms-gateway",
    "email-worker",
    "report-gen",
    "audit-log",
    "config-center",
    "scheduler",
    "quota-guard",
    "file-store",
    "image-thumb",
    "cdn-purge",
    "session-cache",
    "rate-limiter",
    "webhook-hub",
    "data-sync",
    "etl-nightly",
    "geo-router",
    "feature-flag",
    "cart-svc",
    "inventory-hold",
    "price-engine",
    "coupon-svc",
    "loyalty-points",
    "search-suggest",
    "recommend-api",
    "feed-rank",
    "push-gateway",
    "ws-hub",
    "presence-svc",
    "chat-log",
    "media-transcode",
    "thumb-cdn",
    "stat-rollup",
]

ALIAS_FRAGMENTS = ["svc", "api", "worker", "gw", "svc2", "old", "v2", "core", "edge", "bak"]

INFO_MESSAGES = [
    "request completed",
    "cache hit",
    "connection pooled",
    "healthcheck ok",
    "batch flushed",
    "lease renewed",
    "checkpoint saved",
    "queue drained",
    "session refreshed",
    "index compacted",
    "token rotated",
    "prefetch done",
]
WARN_MESSAGES = [
    "slow query 1.8s",
    "retrying upstream",
    "cache stampede risk",
    "pool near capacity",
    "clock skew 350ms",
    "backpressure applied",
    "stale config detected",
]
ERR_MESSAGES = [
    "upstream timeout after 3 attempts",
    "connection reset by peer",
    "unexpected payload shape",
    "deadlock suspected, aborting tx",
    "shard unavailable",
    "schema mismatch on decode",
    "disk quota exceeded",
    "handshake failed",
    "partial write rolled back",
    "lease lost mid-op",
]


def gen_services(rng: random.Random) -> list[dict]:
    services = []
    for i, name in enumerate(SERVICE_NAMES[:N_SERVICES]):
        frag = rng.sample(ALIAS_FRAGMENTS, 2)
        aliases = [f"{name.split('-')[0]}-{frag[0]}", f"{name.replace('-', '_')}_{frag[1]}"]
        services.append({"svc_id": f"SVC_{i:02d}", "name": name, "aliases": aliases})
    return services


def gen_log_line(rng: random.Random, day: int, services: list[dict]) -> tuple[str, str | None]:
    """返回 (行文本, E41行的服务name或None)。"""
    svc = rng.choice(services)
    ref = rng.choice([svc["svc_id"], *svc["aliases"]])
    hh, mm, ss = rng.randrange(24), rng.randrange(60), rng.randrange(60)
    roll = rng.random()
    if roll < 0.06:  # ERROR
        code = rng.choice(["E41xx", "E41yy", "E41zz", "E4001", "E5033", "E4099"])
        level, msg = "ERROR", rng.choice(ERR_MESSAGES)
        line = f"2026-01-0{day} {hh:02d}:{mm:02d}:{ss:02d} {level} [{ref}] code={code} {msg}"
        return line, (svc["name"] if code.startswith("E41") else None)
    if roll < 0.22:
        return (
            f"2026-01-0{day} {hh:02d}:{mm:02d}:{ss:02d} WARN [{ref}] code=W{rng.randrange(100, 999)} {rng.choice(WARN_MESSAGES)}",
            None,
        )
    return f"2026-01-0{day} {hh:02d}:{mm:02d}:{ss:02d} INFO [{ref}] {rng.choice(INFO_MESSAGES)}", None


def main() -> None:
    rng = random.Random(SEED)
    base = Path(__file__).resolve().parent
    files = base / "files"
    services = gen_services(rng)
    (files / "services.json").write_text(json.dumps(services, ensure_ascii=False, indent=1), encoding="utf-8")

    counts: Counter[str] = Counter()
    logs_dir = files / "logs"
    logs_dir.mkdir(exist_ok=True)
    for day in range(1, N_DAYS + 1):
        lines = []
        for _ in range(LINES_PER_LOG):
            line, hit = gen_log_line(rng, day, services)
            lines.append(line)
            if hit:
                counts[hit] += 1
        (logs_dir / f"app-2026-01-0{day}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = "".join(f"{name}:{cnt}\n" for name, cnt in sorted(counts.items()) if cnt > 0)
    (base / "expected_report.txt").write_text(report, encoding="utf-8")
    print(f"services={len(services)} e41_total={sum(counts.values())} services_hit={len(counts)}")


if __name__ == "__main__":
    main()
