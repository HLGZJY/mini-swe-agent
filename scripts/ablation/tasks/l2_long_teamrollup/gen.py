"""L2 数据生成器：确定性（seed 固定），生成 服务/团队 两级映射 + 8 天日志 + 期望报告。

报告口径：按**团队**聚合 E41yy 事件数（服务→团队在 teams.json 的 members 里），
全部团队按名字升序输出 `team:count`（0 命中的团队不输出）。
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

SEED = 20261011
N_SERVICES = 36
N_TEAMS = 7
N_DAYS = 8
LINES_PER_LOG = 800

SERVICE_NAMES = sorted(
    {
        f"cap-{w}-{i:02d}"
        for w in ["core", "edge", "data", "risk", "flow", "mesh", "grid", "vault", "relay", "beacon", "spark", "drift"]
        for i in range(3)
    }
)[:N_SERVICES]

TEAM_NAMES = ["atlas", "borealis", "cascade", "domino", "ember", "fathom", "glacier"]

ALIAS_FRAGMENTS = ["svc", "api", "old", "v2", "core", "bak"]

INFO_MESSAGES = ["positions synced", "hedge rebalanced", "cursor committed", "window rotated"]
WARN_MESSAGES = ["lag 12s", "rebalance pending", "snapshot stale"]
ERR_MESSAGES = ["corridor down", "book drift", "settle failed", "grid partition"]


def gen_world(rng: random.Random) -> tuple[list[dict], dict]:
    services = []
    for i, name in enumerate(SERVICE_NAMES):
        frag = rng.sample(ALIAS_FRAGMENTS, 2)
        # 全名前缀保证别名全局唯一（服务名唯一 ⇒ 别名唯一，无归并歧义）
        aliases = [f"{name}-{frag[0]}", f"{name.replace('-', '_')}.{frag[1]}"]
        services.append({"svc_id": f"CS_{i:03d}", "name": name, "aliases": aliases})
    ids = [s["svc_id"] for s in services]
    rng.shuffle(ids)
    teams = {}
    members = {}
    for i, team in enumerate(TEAM_NAMES[:N_TEAMS]):
        take = ids[i::N_TEAMS]
        teams[team] = take
        for sid in take:
            members[sid] = team
    return services, {"teams": teams, "members": members}


def gen_line(rng: random.Random, day: int, services: list[dict], members: dict) -> tuple[str, str | None]:
    svc = rng.choice(services)
    ref = rng.choice([svc["svc_id"], *svc["aliases"]])
    hh, mm, ss = rng.randrange(24), rng.randrange(60), rng.randrange(60)
    roll = rng.random()
    if roll < 0.055:
        code = rng.choice(["E41yy", "E41xx", "E43yy", "E52xx"])
        line = f"2026-03-{day:02d} {hh:02d}:{mm:02d}:{ss:02d} ERROR [{ref}] code={code} {rng.choice(ERR_MESSAGES)}"
        return line, (members.get(svc["svc_id"]) if code == "E41yy" else None)
    if roll < 0.21:
        return f"2026-03-{day:02d} {hh:02d}:{mm:02d}:{ss:02d} WARN [{ref}] code=W{rng.randrange(100, 999)} {rng.choice(WARN_MESSAGES)}", None
    return f"2026-03-{day:02d} {hh:02d}:{mm:02d}:{ss:02d} INFO [{ref}] {rng.choice(INFO_MESSAGES)}", None


def main() -> None:
    rng = random.Random(SEED)
    base = Path(__file__).resolve().parent
    files = base / "files"
    services, world = gen_world(rng)
    (files / "services.json").write_text(json.dumps(services, ensure_ascii=False, indent=1), encoding="utf-8")
    (files / "teams.json").write_text(json.dumps(world["teams"], ensure_ascii=False, indent=1), encoding="utf-8")

    counts: Counter[str] = Counter()
    logs = files / "logs"
    logs.mkdir(exist_ok=True)
    for day in range(1, N_DAYS + 1):
        lines = []
        for _ in range(LINES_PER_LOG):
            line, hit = gen_line(rng, day, services, world["members"])
            lines.append(line)
            if hit:
                counts[hit] += 1
        (logs / f"capsvc-2026-03-{day:02d}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = "".join(f"{t}:{c}\n" for t, c in sorted(counts.items()) if c > 0)
    (base / "expected_report.txt").write_text(report, encoding="utf-8")
    print(f"services={len(services)} teams_hit={len(counts)} e41yy_total={sum(counts.values())}")


if __name__ == "__main__":
    main()
