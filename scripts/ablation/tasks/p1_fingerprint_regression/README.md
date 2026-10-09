# 任务 P1：指纹碰撞回归（常规桶 / bug 修复）

题源：`oncall-copilot/src/oncall/ingest/fingerprint.py`（真实模块，带设计文档契约）。
注入回归：`_hash` 的 0xFF 分隔被「简化」掉（docstring 里的注释保留了这段虚构评审史），
导致不同 label 组合拼接后碰撞。

## 判定

checker = 隐藏测试（任务目录内，不进 workdir）：
- `test_separator_prevents_concat_collision`：`ab`+`c` 与 `a`+`bc` 必须不同指纹（bug 下碰撞，失败）
- `test_bucket_boundary_separated`：桶序号与 label 段之间也要有分隔（bug 下 `label+bucket` 可碰撞）
- 加上公开测试全集（行为保持：稳定 / 窗口移动 / naive 时间 / 缺 label）

solved = 隐藏测试全绿。
