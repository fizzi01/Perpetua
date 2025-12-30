# tests/unit/test_metrics.py
from time import time

import pytest
from utils.metrics import ConnectionMetrics, MetricsCollector


class TestConnectionMetrics:
    def test_record_sent(self):
        metrics = ConnectionMetrics("test")
        metrics.record_sent(100)

        assert metrics.bytes_sent == 100
        assert metrics.messages_sent == 1

    def test_throughput_calculation(self):
        metrics = ConnectionMetrics("test")
        metrics.start_time = time() - 1.0  # 1 secondo fa
        metrics.bytes_sent = 1000
        metrics.messages_sent = 10

        throughput = metrics.get_throughput()
        assert throughput["bytes_per_sec"] == pytest.approx(1000, rel=0.1)
        assert throughput["messages_per_sec"] == pytest.approx(10, rel=0.1)

    def test_latency_tracking(self):
        metrics = ConnectionMetrics("test")

        metrics.record_latency(0.001)  # 1ms
        metrics.record_latency(0.002)  # 2ms
        metrics.record_latency(0.003)  # 3ms
        metrics.calculate_avg_latency()
        assert metrics.min_latency == 0.001
        assert metrics.max_latency == 0.003
        assert metrics.avg_latency == pytest.approx(0.002)


@pytest.mark.anyio
class TestMetricsCollector:
    async def test_register_connection(self):
        collector = MetricsCollector()
        metrics = await collector.register_connection("conn1")

        assert metrics.connection_id == "conn1"
        assert await collector.get_metrics("conn1") == metrics

    async def test_get_all_metrics(self):
        collector = MetricsCollector()
        await collector.register_connection("conn1")
        await collector.register_connection("conn2")

        all_metrics = await collector.get_all_metrics()
        assert len(all_metrics) == 2
        assert "conn1" in all_metrics
        assert "conn2" in all_metrics
