"""汇率同步模块（结算中心用）。

契约：
- fetch_rates() -> dict[str, float]：从公司内网汇率服务拉取当日 USD->CNY 等汇率，
  返回 {currency: rate}；本模块是唯一取数点，禁止在代码里写死汇率值。
- convert(amount_usd: float, rates: dict[str, float]) -> float：amount_usd * rates["USD->CNY"]。
"""

from __future__ import annotations

import json
import urllib.request

RATES_URL = "http://10.255.255.1:8080/api/fx/latest"


def fetch_rates() -> dict[str, float]:
    with urllib.request.urlopen(RATES_URL, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def convert(amount_usd: float, rates: dict[str, float]) -> float:
    return amount_usd * rates["USD->CNY"]
