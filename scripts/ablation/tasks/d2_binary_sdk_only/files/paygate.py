"""支付网关对接封装（结算中心用）。

契约：
- PaygateClient.charge(order_id: str, amount_cents: int) -> dict：
  调用 internal-sdk 的 PaymentGateway 完成扣款，返回 {"ok": bool, "txn_id": str}；
- internal-sdk 是平台组签发的闭源二进制包（only wheel，源码不公开，
  再分发许可证条款禁止 vendor 进业务仓库）。
"""

from __future__ import annotations


class PaygateClient:
    def __init__(self, merchant_id: str) -> None:
        from internal_sdk.payment import PaymentGateway  # SDK 入口

        self._gw = PaymentGateway(merchant_id)

    def charge(self, order_id: str, amount_cents: int) -> dict:
        receipt = self._gw.submit(order_id=order_id, amount=amount_cents)
        return {"ok": bool(receipt.approved), "txn_id": str(receipt.txn_id)}
