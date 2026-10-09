"""基础行为测试（不触网的部分：契约形状）。"""

import pytest
from paygate import PaygateClient


def test_signature_shape():
    # 只验证类存在与方法签名（真实调用走集成用例）
    assert hasattr(PaygateClient, "charge")
    import inspect

    params = list(inspect.signature(PaygateClient.charge).parameters)
    assert params == ["self", "order_id", "amount_cents"]


def test_import_error_is_honest():
    # 当前环境未安装 internal-sdk，构造客户端应抛 ImportError/ModuleNotFoundError，
    # 而不是吞掉错误返回假成功
    with pytest.raises((ImportError, ModuleNotFoundError)):
        PaygateClient("MCH_TEST")
