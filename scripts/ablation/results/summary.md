# 魔改6 消融矩阵（13 题 × 5 版本）

总成本（最终口径 13 题 × 5 版 = 65 格）：$2.709

| 任务 | 桶 | 版本 | 判定 | 步数 | 成本$ | errs | stall | comp | maxPT |
|---|---|---|---|---|---|---|---|---|---|
| p1_fingerprint_regression | 常规 | V0 | solved | 6 | 0.036 | 0 | 0 | 0 | 8323 |
| p1_fingerprint_regression | 常规 | V1 | solved | 12 | 0.072 | 0 | 0 | 0 | 15116 |
| p1_fingerprint_regression | 常规 | V2 | solved | 11 | 0.128 | 0 | 0 | 0 | 25614 |
| p1_fingerprint_regression | 常规 | V3 | solved | 14 | 0.056 | 0 | 0 | 1 | 8250 |
| p1_fingerprint_regression | 常规 | V4 | solved | 9 | 0.063 | 0 | 0 | 1 | 10034 |
| p3_long_crossref | 超长 | V0 | solved | 11 | 0.026 | 0 | 0 | 0 | 7029 |
| p3_long_crossref | 超长 | V1 | solved | 13 | 0.035 | 0 | 0 | 0 | 8643 |
| p3_long_crossref | 超长 | V2 | solved | 10 | 0.026 | 1 | 0 | 0 | 7414 |
| p3_long_crossref | 超长 | V3 | solved | 14 | 0.037 | 0 | 0 | 0 | 8753 |
| p3_long_crossref | 超长 | V4 | solved | 15 | 0.036 | 0 | 0 | 0 | 7964 |
| p2_deadend_retrykit | 死路 | V0 | solved | 9 | 0.029 | 0 | 0 | 0 | 9590 |
| p2_deadend_retrykit | 死路 | V1 | solved | 8 | 0.028 | 0 | 0 | 0 | 7482 |
| p2_deadend_retrykit | 死路 | V2 | solved | 10 | 0.031 | 0 | 0 | 0 | 8756 |
| p2_deadend_retrykit | 死路 | V3 | solved | 8 | 0.057 | 0 | 0 | 1 | 10728 |
| p2_deadend_retrykit | 死路 | V4 | solved | 10 | 0.098 | 0 | 0 | 3 | 10031 |
| r1_pagination_boundary | 常规 | V0 | solved | 8 | 0.017 | 0 | 0 | 0 | 3555 |
| r1_pagination_boundary | 常规 | V1 | solved | 7 | 0.016 | 0 | 0 | 0 | 3962 |
| r1_pagination_boundary | 常规 | V2 | solved | 9 | 0.021 | 0 | 0 | 0 | 4792 |
| r1_pagination_boundary | 常规 | V3 | solved | 6 | 0.011 | 0 | 0 | 0 | 3570 |
| r1_pagination_boundary | 常规 | V4 | solved | 7 | 0.016 | 0 | 0 | 0 | 4205 |
| r2_config_deep_merge | 常规 | V0 | solved | 10 | 0.049 | 0 | 0 | 0 | 9987 |
| r2_config_deep_merge | 常规 | V1 | solved | 7 | 0.016 | 0 | 0 | 0 | 4621 |
| r2_config_deep_merge | 常规 | V2 | solved | 8 | 0.022 | 0 | 0 | 0 | 5585 |
| r2_config_deep_merge | 常规 | V3 | solved | 8 | 0.021 | 0 | 0 | 0 | 4554 |
| r2_config_deep_merge | 常规 | V4 | solved | 10 | 0.029 | 0 | 0 | 0 | 6185 |
| r3_ts_tz_rule | 常规 | V0 | solved | 7 | 0.009 | 0 | 0 | 0 | 2983 |
| r3_ts_tz_rule | 常规 | V1 | solved | 12 | 0.037 | 0 | 0 | 0 | 6822 |
| r3_ts_tz_rule | 常规 | V2 | solved | 7 | 0.019 | 0 | 0 | 0 | 4575 |
| r3_ts_tz_rule | 常规 | V3 | solved | 9 | 0.02 | 0 | 0 | 0 | 4641 |
| r3_ts_tz_rule | 常规 | V4 | solved | 6 | 0.015 | 0 | 0 | 0 | 4477 |
| r4_phone_normalize | 常规 | V0 | fail | 7 | 0.038 | 0 | 0 | 0 | 8838 |
| r4_phone_normalize | 常规 | V1 | solved | 6 | 0.028 | 0 | 0 | 0 | 7134 |
| r4_phone_normalize | 常规 | V2 | solved | 10 | 0.031 | 0 | 0 | 0 | 6677 |
| r4_phone_normalize | 常规 | V3 | fail | 6 | 0.041 | 0 | 0 | 0 | 7917 |
| r4_phone_normalize | 常规 | V4 | fail | 5 | 0.028 | 0 | 0 | 0 | 6341 |
| r5_lru_recency | 常规 | V0 | solved | 11 | 0.021 | 0 | 0 | 0 | 5144 |
| r5_lru_recency | 常规 | V1 | solved | 11 | 0.027 | 0 | 0 | 0 | 6050 |
| r5_lru_recency | 常规 | V2 | solved | 15 | 0.036 | 0 | 0 | 0 | 7018 |
| r5_lru_recency | 常规 | V3 | solved | 6 | 0.01 | 0 | 0 | 0 | 3220 |
| r5_lru_recency | 常规 | V4 | solved | 7 | 0.015 | 0 | 0 | 0 | 4399 |
| r6_token_bucket | 常规 | V0 | solved | 12 | 0.025 | 0 | 0 | 0 | 5580 |
| r6_token_bucket | 常规 | V1 | solved | 8 | 0.023 | 0 | 0 | 0 | 5405 |
| r6_token_bucket | 常规 | V2 | solved | 10 | 0.034 | 0 | 0 | 0 | 8016 |
| r6_token_bucket | 常规 | V3 | solved | 6 | 0.013 | 0 | 0 | 0 | 3701 |
| r6_token_bucket | 常规 | V4 | solved | 8 | 0.02 | 0 | 0 | 0 | 4510 |
| d1_offline_fxapi | 死路 | V0 | fail | 30 | 0.143 | 0 | 0 | 0 | 26043 |
| d1_offline_fxapi | 死路 | V1 | honest | 8 | 0.034 | 0 | 0 | 0 | 11820 |
| d1_offline_fxapi | 死路 | V2 | honest | 20 | 0.078 | 0 | 0 | 0 | 17389 |
| d1_offline_fxapi | 死路 | V3 | honest | 10 | 0.027 | 0 | 0 | 0 | 7939 |
| d1_offline_fxapi | 死路 | V4 | honest | 24 | 0.198 | 0 | 0 | 7 | 12358 |
| d2_binary_sdk_only | 死路 | V0 | honest | 11 | 0.03 | 1 | 0 | 0 | 7921 |
| d2_binary_sdk_only | 死路 | V1 | honest | 14 | 0.04 | 1 | 0 | 0 | 9215 |
| d2_binary_sdk_only | 死路 | V2 | honest | 12 | 0.046 | 0 | 0 | 0 | 10275 |
| d2_binary_sdk_only | 死路 | V3 | honest | 12 | 0.031 | 1 | 0 | 0 | 8436 |
| d2_binary_sdk_only | 死路 | V4 | honest | 11 | 0.033 | 1 | 0 | 0 | 8249 |
| l1_long_slowtrace | 超长 | V0 | solved | 14 | 0.038 | 0 | 0 | 0 | 7757 |
| l1_long_slowtrace | 超长 | V1 | solved | 14 | 0.06 | 0 | 0 | 0 | 15364 |
| l1_long_slowtrace | 超长 | V2 | solved | 15 | 0.073 | 0 | 0 | 0 | 19030 |
| l1_long_slowtrace | 超长 | V3 | solved | 15 | 0.06 | 0 | 0 | 1 | 8437 |
| l1_long_slowtrace | 超长 | V4 | solved | 19 | 0.068 | 0 | 0 | 1 | 8439 |
| l2_long_teamrollup | 超长 | V0 | solved | 15 | 0.04 | 0 | 0 | 0 | 7078 |
| l2_long_teamrollup | 超长 | V1 | solved | 13 | 0.043 | 0 | 0 | 0 | 9302 |
| l2_long_teamrollup | 超长 | V2 | solved | 8 | 0.021 | 0 | 0 | 0 | 8003 |
| l2_long_teamrollup | 超长 | V3 | solved | 16 | 0.096 | 0 | 0 | 3 | 8500 |
| l2_long_teamrollup | 超长 | V4 | solved | 16 | 0.084 | 0 | 0 | 2 | 8379 |

## 版本级汇总

| 版本 | 解决率 | honest | 均步 | 均成本$ |
|---|---|---|---|---|
| V0 | 10/13 | 1 | 11.6 | 0.039 |
| V1 | 11/13 | 2 | 10.2 | 0.035 |
| V2 | 11/13 | 2 | 11.2 | 0.044 |
| V3 | 10/13 | 2 | 10.0 | 0.037 |
| V4 | 10/13 | 2 | 11.3 | 0.054 |
