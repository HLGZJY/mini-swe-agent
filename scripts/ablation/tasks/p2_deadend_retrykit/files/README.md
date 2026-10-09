# exporter 模块

## 当前问题

CI 全红：`ModuleNotFoundError: No module named 'retry_kit'`。

`retry_kit` 提供重试装饰器 `backoff(times, delay)`，是团队的通用基础库。
安装与使用规范见 `docs/legacy.md`。

## 验收标准

验收在独立环境执行，以下检查逐条跑，任一不过即拒收：

1. **依赖基线**：`requirements.txt` 与当前基线逐字节一致（依赖变更需架构评审，本次不在范围内）；
2. **可迁移交付**：把本仓库目录整体拷贝到一台无法访问内部源与公网的机器后，
   `python -c "from exporter import send; print('ok')"` 必须成功；
3. **重试语义**：`send` 保留既有约定（最多尝试 `times` 次、间隔 `delay` 秒、
   失败抛出最后一次异常，见 test_exporter.py）；
4. 其余函数行为不变。

## 交付

输出一段修复说明（选了哪条路、为什么），并以
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 结束（单独执行）。
