# retry_kit 安装（历史文档，最后更新 2026-06-30）

`retry_kit` 是团队内部通用重试库，提供 `backoff(times, delay)` 装饰器。

安装：

    pip install retry-kit==2.1.0 --index-url https://internal-pkg.example.com/simple

> 注意：需要连接公司内网或 VPN。拆分期（2026-06）各服务仓库统一从 monorepo 迁出，
> 依赖统一走内部源，请勿将内部包源码直接拷贝进业务仓库（架构红线 AR-118）。

如有安装问题联系平台组。
