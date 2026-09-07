"""健康探测回归测试：真实 async PG/Redis 探测（契约环境依赖真实可用）。

覆盖踩坑：Windows ProactorEventLoop 不支持 psycopg async——create_app 必须切换
SelectorEventLoop，否则 /health/ready 恒 not_ready（本用例在真实探测下断言 ready）。
"""

from __future__ import annotations


def test_ready_with_real_dependencies(client):
    response = client.http.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["redis"] == "ok"
