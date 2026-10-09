#!/usr/bin/env python3
"""魔改6 单格执行器：一个 (task, version) 格子 = 一次真 LM run。

由 run.py 以子进程方式调起（隔离 + 可外部看门狗）。职责：
  1. 按版本设环境变量（必须在 import minisweagent 之前——模型层 retry 在导入期应用）
  2. 复制任务文件到独立 workdir（agent 摸不到判定器）
  3. base.yaml + 版本 overlay + 运行时键 递归合并成最终配置
  4. 可选 patch（V1 关工具层重试：ToolSpec.max_retries = 0，registry 进程内单例）
  5. DefaultAgent 跑任务，写 run.traj.json / session.db / result.json

用法：
  .venv/Scripts/python.exe _run_one.py --task-dir <dir> --version v2_retry --cell-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", required=True, help="任务目录（含 task.yaml / files/ / checker.py）")
    parser.add_argument("--version", required=True, help="版本键（见 versions.py）")
    parser.add_argument("--cell-dir", required=True, help="本格输出目录（traj/session/result 都写这里）")
    args = parser.parse_args()

    sys.path.insert(0, str(HERE))
    from versions import VERSIONS  # noqa: E402  —— 不依赖 minisweagent，可安全先导入

    if args.version not in VERSIONS:
        print(f"Unknown version: {args.version}", file=sys.stderr)
        return 2
    ver = VERSIONS[args.version]

    # 1) 版本环境变量必须先于 minisweagent 导入设置（retry.py 在导入期读 env）
    for key, value in ver.get("env", {}).items():
        os.environ[key] = value

    cell_dir = Path(args.cell_dir).resolve()
    cell_dir.mkdir(parents=True, exist_ok=True)
    workdir = cell_dir / "workdir"
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(Path(args.task_dir) / "files", workdir)

    import yaml  # noqa: E402

    # 2-4) minisweagent 导入 + patch
    from minisweagent.agents.default import DefaultAgent  # noqa: E402
    from minisweagent.environments.local import LocalEnvironment  # noqa: E402
    from minisweagent.models.litellm_model import LitellmModel  # noqa: E402
    from minisweagent.utils.serialize import recursive_merge  # noqa: E402

    if "disable_tool_retry" in ver.get("patch", []):
        from minisweagent.models.utils.tool_registry import get_default_registry  # noqa: E402

        registry = get_default_registry()
        patched = []
        for name in ("read_file", "grep", "list_dir"):
            spec = registry.get(name)
            if spec is not None:
                spec.max_retries = 0
                patched.append(name)
        print(f"[patch] disable_tool_retry: max_retries=0 -> {patched}")

    base = yaml.safe_load((HERE / "base.yaml").read_text(encoding="utf-8"))
    task_yaml = yaml.safe_load((Path(args.task_dir) / "task.yaml").read_text(encoding="utf-8"))
    prompt = task_yaml["prompt"].rstrip()

    runtime = {
        "agent": {
            "output_path": str(cell_dir / "run.traj.json"),
            "session_db_path": str(cell_dir / "session.db"),
        },
        "environment": {"cwd": str(workdir)},
    }
    overlays: list[dict] = [base]
    if ver.get("model"):
        overlays.append({"model": ver["model"]})
    if ver.get("agent"):
        overlays.append({"agent": ver["agent"]})
    overlays.append(runtime)
    config = recursive_merge(*overlays)

    # 5) 跑
    model = LitellmModel(**config["model"])
    env = LocalEnvironment(**config["environment"])
    agent = DefaultAgent(model, env, **config["agent"])
    result = agent.run(prompt)
    traj = agent.save(Path(config["agent"]["output_path"]))

    info = traj.get("info", {})
    stats = info.get("model_stats", {})
    outcome = {
        "task": Path(args.task_dir).name,
        "version": args.version,
        "exit_status": info.get("exit_status", ""),
        "run_exit_status": result.get("exit_status", ""),
        "submission": (info.get("submission", "") or "")[:2000],
        "instance_cost": stats.get("instance_cost", 0.0),
        "api_calls": stats.get("api_calls", 0),
        "workdir": str(workdir),
    }
    (cell_dir / "result.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: outcome[k] for k in ("exit_status", "instance_cost", "api_calls")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
