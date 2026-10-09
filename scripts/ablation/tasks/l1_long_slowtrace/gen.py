"""L1 数据生成器：确定性（seed 固定），生成 services.json + 10 天日志 + 期望报告。

报告口径与 P3 不同：只统计 E41xx 一个错误码，且只报 Top-5（次数降序，
次数同则服务名升序），格式 `name:count`。
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

SEED = 20261010
N_SERVICES = 48
N_DAYS = 10
LINES_PER_LOG = 900

SERVICE_NAMES = [
    "ledger-api", "fx-feed", "settle-batch", "recon-worker", "voucher-svc",
    "invoice-gen", "tax-calc", "payout-gw", "refund-handler", "chargeback-svc",
    "balance-svc", "statement-gen", "swift-gw", "iso8583-hub", "card-auth",
    "fraud-check", "kyc-verify", "aml-screen", "limit-guard", "fee-calc",
    "gl-poster", "subledger", "treasury-api", "liquidity-mon", "sweep-worker",
    "nostro-rec", "vostro-rec", "eod-close", "holiday-cal", "fx-hedge",
    "rate-lock", "quote-cache", "mid-price", "curve-build", "yield-svc",
    "spread-calc", "nav-calc", "pos-keeper", "margin-call", "collateral",
    "pledge-svc", "repo-engine", "coupon-pay", "redemption", "transfer-api",
    "ach-gw", "sepa-gw", "chaps-gw",
]

ALIAS_FRAGMENTS = ["svc", "api", "gw", "worker", "old", "v2", "core", "edge"]

INFO_MESSAGES = [
    "ledger posted", "batch committed", "idempotency hit", "recon matched",
    "balance snapshot", "fee waived", "fx locked", "settlement window opened",
]
WARN_MESSAGES = [
    "recon lag 45min", "posting queue deep", "fx staleness 3s",
    "duplicate suppressed", "cutoff approaching",
]
ERR_MESSAGES = [
    "double-post suspected", "leg mismatch", "cutoff missed",
    "corridor suspended", "reversal failed",
]


def gen_services(rng: random.Random) -> list[dict]:
    services = []
    for i, name in enumerate(SERVICE_NAMES[:N_SERVICES]):
        frag = rng.sample(ALIAS_FRAGMENTS, 2)
        aliases = [f"{name.split('-')[0]}-{frag[0]}", f"{name.replace('-', '_')}{frag[1]}"]
        services.append({"svc_id": f"LS_{i:03d}", "name": name, "aliases": aliases})
    return services


def gen_line(rng: random.Random, day: int, services: list[dict]) -> tuple[str, str | None]:
    svc = rng.choice(services)
    ref = rng.choice([svc["svc_id"], *svc["aliases"]])
    hh, mm, ss = rng.randrange(24), rng.randrange(60), rng.randrange(60)
    roll = rng.random()
    if roll < 0.05:
        code = rng.choice(["E41xx", "E41yy", "E41zz", "E52xx", "E6001"])
        line = f"2026-02-0{day % 10} {hh:02d}:{mm:02d}:{ss:02d} ERROR [{ref}] code={code} {rng.choice(ERR_MESSAGES)}"
        return line, (svc["name"] if code == "E41xx" else None)
    if roll < 0.20:
        return f"2026-02-0{day % 10} {hh:02d}:{mm:02d}:{ss:02d} WARN [{ref}] code=W{rng.randrange(100, 999)} {rng.choice(WARN_MESSAGES)}", None
    return f"2026-02-0{day % 10} {hh:02d}:{mm:02d}:{ss:02d} INFO [{ref}] {rng.choice(INFO_MESSAGES)}", None


def main() -> None:
    rng = random.Random(SEED)
    base = Path(__file__).resolve().parent
    files = base / "files"
    services = gen_services(rng)
    (files / "services.json").write_text(json.dumps(services, ensure_ascii=False, indent=1), encoding="utf-8")

    counts: Counter[str] = Counter()
    logs = files / "logs"
    logs.mkdir(exist_ok=True)
    for day in range(1, N_DAYS + 1):
        lines = []
        for _ in range(LINES_PER_LOG):
            line, hit = gen_line(rng, day, services)
            lines.append(line)
            if hit:
                counts[hit] += 1
        (logs / f"ledger-2026-02-{day:02d}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    top5 = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    report = "".join(f"{name}:{cnt}\n" for name, cnt in top5)
    (base / "expected_report.txt").write_text(report, encoding="utf-8")
    print(f"services={len(services)} e41xx_total={sum(counts.values())} hit={len(counts)}")


if __name__ == "__main__":
    main()
