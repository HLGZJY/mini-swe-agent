# Fork Modifications（魔改速览）

本 fork 基于 upstream v2.4.6（`04d809ce`），围绕「生产 Harness 缺什么」做了 7 个模块化魔改。
每个魔改一段：**动机 → 做法 → 数字**。全部数字来自 13 题 × 5 版本增量消融（真 LM，kimi-k2.6），
完整 65 格明细与复现方法见
[scripts/ablation/results/summary.md](https://github.com/HLGZJY/mini-swe-agent/blob/main/scripts/ablation/results/summary.md)。

| # | 模块 | 一句话结果 |
|---|---|---|
| 0 | Windows 兼容 / Shell 可配置 | 上游测试套件本机 38 failed → 5 failed |
| 1 | 结构化 Tool Calling | 死路任务成本 -76%，fail→honest 行为质变 |
| 2 | 失败语义与重试 | error_class 六类；幂等性决定重试权 |
| 3 | Context 压缩 | 峰值上下文 26k→8-12k；防爆窗、不省钱（诚实负向） |
| 4 | Session 持久化 | kill -9 后断点恢复，LM 零重烧 |
| 5 | 停滞检测 | 步指纹 + 阶梯干预，65 格零误伤 |
| 6 | 消融评测 | 85 runs / $3.50，量化每个魔改"值多少" |

## 魔改 0 · Windows 兼容与 Shell 可配置化

**动机**：上游 `LocalEnvironment` 固定 `subprocess(shell=True)`，Windows 上等于把 agent 的命令交给 cmd.exe，而提示词与全部测试假设 POSIX 语义——完成标记永不匹配，agent 一路烧满预算。
**做法**：判定层剥一层配对引号（同段判定逻辑在 7 个环境文件里逐字重复，提取公共函数净减 63 行）；命令层新增 `environment.shell: auto|bash|cmd|powershell`，`auto` 先实测 `bash -c "echo ok"` 再启用（防 System32 的 WSL bash 陷阱），探测结果写入轨迹 `info.shell`。
**数字**：本机上游套件 38 failed → 5 failed（350 → 415 passed），剩余 5 个逐个归因为环境性；新增回归 0。

## 魔改 1 · 结构化 Tool Calling（Tool Registry + Schema 校验 + 错误自愈）

**动机**：bash-only 无 Schema 校验、无权限粒度、无错误语义，参数传错只能等运行时炸。
**做法**：Tool Registry（`bash` / `read_file` / `grep` / `list_dir`），pydantic 作单一事实源——`model_json_schema()` 广播给 LM、`model_validate()` 本地校验，描述与校验永不同源漂移；**校验失败不崩**：错误文本 + 期望 Schema 作为观察回填，模型下一轮自己修；与 FormatError 严格分层（协议坏整轮报废 vs 参数错只废这一个 call）。
**数字**：死路题 d1——V0 bash-only 烧满 30 步 $0.143 → V1 8 步 $0.034 诚实申报（**成本 -76% + fail→honest 质变**）；诚实反例：结构清晰的修复题上 TC 是纯开销（p1：6 步 $0.036 vs 12 步 $0.072）——TC 的价值随任务探索程度递增，不是普胜。

## 魔改 2 · Tool 失败语义（错误分类 + 声明式重试 + 受控终止）

**动机**：执行失败只有退出码与 stderr 文本，可恢复错误与致命错误混在同一通道。
**做法**：`classify_exception` 单一事实源打 **error_class 六类机器可读标签**（timeout / permission / env_unavailable / tool_error / invalid_params / unknown_tool），永久类带引导文案回填触发 Replan；**幂等性决定重试权**——bash 副作用不可声明、框架永不自动重试，结构化工具经 `ToolSpec.transient_errors` 声明后指数退避（预算上限）；timeout 定为永久类（重试纯烧预算）；重试耗尽可选受控终止（`on_uncaught_exception: controlled_exit`，默认 raise 保暴露自家 bug）。
**数字**：32 单测 + 混沌演示 3/3 + 真 LM 2/2 四项判卷（$0.01）；消融诚实负向——65 格仅 3 格出现错误事件，Retry 维度在本任务集与强模型下不可观测（如实报 0）。

## 魔改 3 · Context 压缩（五段式摘要 + 轮边界折叠 + 快照保全）

**动机**：历史只增不减、每步全量重发，爆窗即中止；上游唯一截断在 read_file 输出级。
**做法**：真实 `usage.prompt_tokens` 越过阈值（≈窗口/1.5）时，把早期整轮折叠为**五段式结构化摘要**（目标 / 已完成 / 关键产物路径 / 当前计划 / 失败教训），保留最近 N 轮原文；切点只落在 assistant 轮边界（tool_calls/tool 配对构造性保持）；LM 摘要失败自动降级机械折叠（零 LM 兜底）；改写前全量历史快照落盘；信号身份标记防陈旧 usage 重触发；收缩守卫禁止压缩放大历史。
**数字**：压缩版峰值上下文全部压到 8-12k（未压缩最高 26k）；诚实负向——l2 上压缩版成本是 V2 的 **4.6×**（$0.096 vs $0.021：重读税 + 摘要税 + prompt cache 命中 85%→70%），结论：**压缩是爆窗预防，不是省钱手段**。

## 魔改 4 · Session 持久化与断点恢复（SQLite 事件流）

**动机**：上游无任何持久化——轨迹是 `finally` 里覆盖写的单个 JSON，进程一死只剩最后覆盖态，无法查询、无法恢复。
**做法**：append-only 事件流（每条消息一行 INSERT，永不 UPDATE/DELETE）+ view_pos 双轨制（superseded 行保留在表里，"模型当时看到什么"压缩后仍可查）；恢复语义 **at-least-once（工具：悬挂 actions 本地重执行）+ never（LM：不重新 query，钱不重烧）**，对照 Temporal 的 Activity 模型；`SUM(messages.cost) == agent.cost` 可 SQL 对账；CLI 新增 `--session-db / --resume / --list-sessions`。
**数字**：真 LM kill -9 后 resume 五项判卷全过（api_calls 20 = 12+8 无重复、view_pos 连续、原史不被覆盖、交付物 pytest 5 passed），总成本 $0.040。

## 魔改 5 · 防死循环（假性推进停滞检测）

**动机**：上游终止只有步数/成本/时长三类无差别限额，对"看起来一直在执行、任务没有推进"的假性死循环完全无感。
**做法**：双通道步指纹 `hash(normalize(命令) + normalize(观察))` 滑动窗口 8 步不变计一次 strike；阶梯响应——温和提醒（注入 user 消息）→ 强提醒（要求换策略或提交）→ `StalledExceeded` 终止；"无文件改动"信号被显式否决（harness 对文件系统的合法感知只有 action/observation 文本，主动扫盘不可靠且越权）；检测器三标量进 Session 持久化，恢复后不再重送满额窗口。
**数字**：19 单测（零误伤专测 + 递增分页反误报用例）；真 LM 阶梯剧本中强提醒把装死模型拉回如实终止（13 步 $0.016）；消融 65 格 stall_event 全 0——对强模型 + 自包含任务是零误伤纯保险（诚实负向：其设计场景弱模型/环境抖动未触达）。

## 魔改 6 · 消融评测（13 题 × 5 版本增量塔）

**动机**：没有数字的 harness 项目站不住——"每个魔改值多少"必须可复现地回答。
**做法**：自建 13 题 × 3 桶（常规 7 / 死路 3 / 超长 3）自包含评测集，判定全自动（solved / fail / honest 三态，honest 需 DB ≥2 条失败证据防偷懒）；增量版本塔 V0 bash-only → V1 +TC → V2 +Retry → V3 +压缩 → V4 +停滞；串行批跑 runner（断点续跑 + 成本护栏）；`aggregate.py` 从 session.db SQL 复现全部数字。
**数字**：85 次真 LM 运行总成本 $3.50（$5 硬顶内），65 格终口径 $2.709。最意外产出——**死路桶行为阶梯**：bash-only 在不可解任务上不止损（d1 打满 30 步 $0.143），带 Tool Calling 的版本全部 8-24 步诚实申报（$0.027-0.046）。

---

- 65 格完整明细：[scripts/ablation/results/summary.md](https://github.com/HLGZJY/mini-swe-agent/blob/main/scripts/ablation/results/summary.md)
- 版本级汇总：V0→V4 解决率 10/11/11/10/10，honest 申报 1→2——版本塔提升的不是解决率，是行为质量（死路止损）与鲁棒性。
- 每个 commit 一个模块（中文提交信息），commit 历史即改造过程的验证材料。
