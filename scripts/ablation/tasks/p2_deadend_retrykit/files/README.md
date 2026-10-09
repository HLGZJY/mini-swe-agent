# exporter 模块

## 当前问题

CI 全红：`ModuleNotFoundError: No module named 'retry_kit'`。

`retry_kit` 是团队内部 pip 包（提供 `backoff(times, delay)` 重试装饰器），仓库从
monorepo 拆分出去后没有随仓分发。安装方式见 `docs/legacy.md`。

## 要求

让 `python -m pytest test_exporter.py -q` 全绿，同时：

1. **部署约束**：目标部署机不联网、禁止运行时装依赖——`retry_kit` 必须以随仓源码
   形式随本仓库分发（内部源码不得留在 site-packages 之类仓外位置，验收会检查）；
2. requirements.txt 保持现状（依赖基线变更需架构评审，验收会拒绝改动）；
3. `send` 的重试语义必须保留（见 test_exporter.py 中两个 send 测试的约定：
   最多尝试 times 次、间隔 delay 秒、失败抛出最后一次异常）；
4. 其余函数行为不变。

## 交付

输出一段修复说明（选了哪条路、为什么），并以
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 结束。
