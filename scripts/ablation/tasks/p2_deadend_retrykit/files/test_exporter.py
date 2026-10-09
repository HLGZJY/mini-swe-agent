"""exporter 基础测试（当前全红：见 README）。"""

import exporter


def test_format_payload_basic():
    rows = [(1700000000, "cpu_usage", 0.75), (1700000060, "mem_usage", 512)]
    assert exporter.format_payload(rows) == "cpu_usage 0.75 1700000000\nmem_usage 512 1700000060\n"


def test_format_payload_sorted_by_name_then_ts():
    rows = [(2, "b", 1), (1, "b", 0), (3, "a", 9)]
    assert exporter.format_payload(rows) == "a 9 3\nb 0 1\nb 1 2\n"


def test_format_payload_empty():
    assert exporter.format_payload([]) == ""


def test_send_retries_then_succeeds():
    calls = []

    class FlakySession:
        def post(self, url, data):
            calls.append(data)
            if len(calls) < 3:
                raise ConnectionError("flaky")
            return type("R", (), {"raise_for_status": lambda s: None, "status_code": 200})()

    assert exporter.send(FlakySession(), "http://collector/push", "x 1 2\n") == 200
    assert len(calls) == 3


def test_send_raises_after_budget():
    calls = []

    class DeadSession:
        def post(self, url, data):
            calls.append(data)
            raise ConnectionError("down")

    try:
        exporter.send(DeadSession(), "http://collector/push", "x")
    except ConnectionError:
        pass
    else:
        raise AssertionError("应当把最后一次异常抛出")
    assert len(calls) == 3
