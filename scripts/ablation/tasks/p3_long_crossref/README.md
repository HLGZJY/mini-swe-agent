# 任务 P3：跨文件别名归并统计（超长桶）

设计目标：步数自然吃满（读 40 服务映射 × 6 日志 × 别名归并 × 汇总校验），且
观察体积可累积到压缩阈值附近（6×260 行日志 ≈ 120KB 文本，cat 2-3 个文件即超
24k token 估算线）。智能 agent 走 grep 路线可省 context——这是诚实变量，不硬
造爆窗；压缩桶的分化信号看「是否逼近阈值 + 是否完成」，pilot 校准
`compression_threshold_tokens`。

## 判定

report.txt 与 gen.py（seed 固定）预生成的 expected_report.txt 逐行一致。
生成器与期望报告都入 git，可复现。
