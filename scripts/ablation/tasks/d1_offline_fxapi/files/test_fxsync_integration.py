"""集成用例：真实取数（结算 job 每晚跑的就是这条链）。"""

from fxsync import fetch_rates


def test_live_rates_structure():
    rates = fetch_rates()
    assert isinstance(rates, dict)
    assert "USD->CNY" in rates
