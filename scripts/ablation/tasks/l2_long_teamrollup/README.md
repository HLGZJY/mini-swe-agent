# L2 超长桶：团队两级归并

从 P3 改造的超长桶变体：8 天 × 800 行（≈290KB）日志、36 服务 → 7 团队
两级映射（services.json 别名归并 + teams.json 服务→团队），
统计口径改为 **E41yy 按团队聚合**（团队名升序）。

两级 join + 别名归并把读面拉长；grep 路线可省 context。
判定 = report.txt 与 expected_report.txt 逐行一致（expected 由 seed 固定的
生成器产出，不进 workdir）。

复跑：`python gen.py`（任务目录下）。
