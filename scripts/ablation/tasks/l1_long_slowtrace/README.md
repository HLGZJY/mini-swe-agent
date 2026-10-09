# L1 超长桶：慢追溯 Top-5 统计

从 P3 改造的超长桶变体：10 天 × 900 行（≈430KB）日志、48 服务别名归并，
统计口径改为 **E41xx 单码 Top-5**（次数降序、平序按名升序）。

设计目标同 P3：数据量保证 context 累积越过压缩阈值；grep 路线可省 context。
判定 = report.txt 与 expected_report.txt 逐行一致（expected 由 seed 固定的
生成器产出，不进 workdir）。

复跑：`python gen.py`（任务目录下）。
