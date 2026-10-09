# scripts/ablation —— 魔改6 消融评测

对五个魔改做增量塔消融：同一批任务 × 5 个版本 × 真 LM（moonshot/kimi-k2.6），
产出简历可用的差异表格（解决率 / 步数 / 成本 / token / 自愈 / 停滞介入）。

## 版本矩阵（增量塔）

| 版本 | tools | 模型层retry | 工具层retry | 压缩 | 停滞 |
|---|---|---|---|---|---|
| v0_bash_only | bash | off | (无只读工具) | - | - |
| v1_tc_noretry | 4 | off | off | - | - |
| v2_retry | 4 | on | on | - | - |
| v3_compress | 4 | on | on | 24000 tok | - |
| v4_stall | 4 | on | on | 24000 tok | window=3 |

重试关闭方式：模型层 env `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=1`；工具层
`_run_one.py` 进程内把 `ToolSpec.max_retries` 置 0（registry 单例，零 src 改动）。

## 任务集（pilot 3 题）

| 任务 | 桶 | 题源 | 判定 |
|---|---|---|---|
| p1_fingerprint_regression | 常规 | oncall-copilot 真实模块注入拼接碰撞回归 | 隐藏 pytest 全绿 |
| p2_deadend_retrykit | 死路 | 拆仓丢内部库的真实场景 | 行为测试 + 依赖红线（requirements 不变、retry_kit 必须 vendor） |
| p3_long_crossref | 超长 | 构造（seed 固定可复现） | report.txt 与期望逐行一致 |

防作弊：每格运行把 `files/` 拷贝到独立 workdir，checker 从任务目录在 workdir 副本
上运行（隐藏测试与期望报告永不进 workdir）。

## 用法

```bash
cd /f/Git\ repository/mini-swe-agent
.venv/Scripts/python.exe scripts/ablation/run.py --batch pilot
# 断点续跑：kill 后原命令重跑，自动跳过已完成格
# 强制重跑某格：--rerun "p1_fingerprint_regression::v2_retry"
# 单格抽查：--tasks p3_long_crossref --versions v3_compress
```

## 产出与口径

- `results/<batch>/preds.json` 进度簿 / `summary.json` 汇总 / 每格 `run.traj.json` + `session.db` + `run.log`
- 指标口径（成文版见 `产出/魔改6-评测.md` 附录，跑数前定稿）：
  - 解决率 = judge=solved / 桶内题数；死路桶另列 honest 提交
  - 步数 = traj `info.model_stats.api_calls`；成本 = `info.model_stats.instance_cost`
  - token = session.db 逐步 usage（无 usage 时 chars/4 估算并标注）
  - retry 自愈 = error_class 事件数与其后 run 存活比例（粗口径）
  - 停滞 = stall_event warning/terminate 计数；挽回 = warning 后指纹打破 / 终局 Submitted
- SQL 抽查要求：表内任一数字可从 session.db 独立复现

## 已知限制（诚实记录）

- 看门狗超时只 kill 子进程直接子代，孙进程（bash 孙命令）可能残留——pilot 用 600s
  agent 上限 + 显式 bash shell 控制风险；放量前验证无残留。
- 压缩桶阈值 24000 token 是 pilot 校准值，可能随任务调整（五个版本同步改，公平性不受影响）。
