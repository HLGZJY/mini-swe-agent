"""指纹模块基础测试（交付前必须全绿；更严格的验收在交付后运行）。"""

from datetime import UTC, datetime, timedelta

import fingerprint as fp

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


def test_same_input_same_fingerprint():
    labels = {"alertname": "HighCPU", "instance": "10.0.0.1", "job": "node"}
    assert fp.canonical_fingerprint(labels, T0) == fp.canonical_fingerprint(labels, T0)


def test_window_bucket_moves_fingerprint():
    labels = {"alertname": "HighCPU", "instance": "10.0.0.1", "job": "node"}
    inside = fp.canonical_fingerprint(labels, T0 + timedelta(minutes=5))
    beyond = fp.canonical_fingerprint(labels, T0 + timedelta(minutes=11))
    assert inside != beyond


def test_naive_timestamp_treated_as_utc():
    labels = {"alertname": "DiskFull", "instance": "10.0.0.2", "job": "node"}
    assert fp.canonical_fingerprint(labels, T0) == fp.canonical_fingerprint(labels, T0.replace(tzinfo=None))


def test_missing_optional_labels_ok():
    fp.canonical_fingerprint({"alertname": "SoleLabel"}, T0)
