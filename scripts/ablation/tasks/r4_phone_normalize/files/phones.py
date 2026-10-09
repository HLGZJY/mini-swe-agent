"""手机号归一化（用户注册与通知中心共用）。

契约：
- normalize_phone(raw: str) -> str：把常见写法归一化为 11 位纯数字国内手机号
  （形如 1xxxxxxxxxx，即 ^1\\d{10}$）。
- 规则（按顺序应用）：
  1. 去除所有空格、连字符 -、点 .、圆括号；
  2. 若以 +86 开头，去掉 +86；再去掉可能残留的前导 0 前缀？——不：
     处理完 +86 后若以 86 开头且总长 13 位，再去掉 86；
  3. 结果必须是 11 位且以 1 开头；不满足则 raise ValueError("invalid phone")。
- 本函数尚未实现（注册链路的最后一环），签名与异常约定如上，不得更改。
"""


def normalize_phone(raw: str) -> str:
    raise NotImplementedError("normalize_phone: 按模块 docstring 契约实现")
