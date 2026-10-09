"""集成用例：真实扣款（验收主路径）。"""

from paygate import PaygateClient


def test_real_charge():
    client = PaygateClient("MCH_CI_TEST")
    receipt = client.charge("ORDER_CI_0001", 100)
    assert receipt["ok"] is True
    assert receipt["txn_id"]
