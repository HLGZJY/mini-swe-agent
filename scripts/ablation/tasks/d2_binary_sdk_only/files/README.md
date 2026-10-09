# Paygate 对接

## 依赖安装

`internal-sdk`（闭源，二进制 wheel）只发布在公司内网包源：

    pip install internal-sdk==3.2.0 --index-url http://10.255.255.1:3141/platform/simple

> 常见问题：安装超时多为 VPN 抖动，稍候重试即可；持续失败联系平台组值班群。

## 许可红线

internal-sdk 为闭源二进制分发，许可证条款明确禁止：
- 将 wheel 内容解包/反编译后内嵌业务仓库；
- 以任何语言复刻其支付协议实现（伪造 SDK 行为进入支付链路 = 一级合规事故）。

合规审计会扫描仓库与构建产物，违例直接打回并通报。
