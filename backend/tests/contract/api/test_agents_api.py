"""Agent 提示词管理 REST 契约测试（单Agent重跑与提示词编辑方案 3.3）。

拓扑静态全图 + has_override 标注；PUT→GET 往返；DELETE 恢复默认；
node_id 含空格/中文 encode 往返；Screening 422 / 未知节点 404。
"""

from __future__ import annotations

import uuid

BASE = "/api/v1/agents"


def test_topology_static_full_graph_with_override_annotation(client):
    response = client.http.get(f"{BASE}/topology")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data.keys()) == {"nodes", "edges", "generated_at"}
    # 全形态：market 5 主节点（含纯代码 Risk Gate）+ sector 3 + screening 1 + stock 12
    assert len(data["nodes"]) == 5 + 3 + 1 + 12
    by_id = {n["id"]: n for n in data["nodes"]}
    screening = by_id["screening:Screening"]
    assert screening["has_prompt"] is False and screening["has_override"] is False
    risk_gate = by_id["market:Risk Gate"]
    assert risk_gate["has_prompt"] is False and risk_gate["has_override"] is False
    cn_news = by_id["market:CN News Analyst"]
    assert cn_news["has_prompt"] is True and cn_news["has_override"] is False
    # 折叠节点（US/KR）不在静态拓扑
    assert "market:US News Analyst" not in by_id


def test_upsert_get_roundtrip_and_reset(client):
    node_id = "market:CN News Analyst"
    put = client.http.put(
        f"{BASE}/prompts/{node_id}", json={"prompt_text": "你是一位资金日历分析师。"})
    assert put.status_code == 200, put.text
    dto = put.json()["data"]
    assert dto["node_id"] == node_id
    assert dto["has_override"] is True
    assert dto["override_prompt"] == "你是一位资金日历分析师。"

    # GET 列表叠加 + 拓扑 has_override 标注
    listing = client.http.get(f"{BASE}/prompts").json()["data"]["items"]
    found = next(d for d in listing if d["node_id"] == node_id)
    assert found["override_prompt"] == "你是一位资金日历分析师。"
    topo = client.http.get(f"{BASE}/topology").json()["data"]
    assert next(n for n in topo["nodes"] if n["id"] == node_id)["has_override"] is True

    # DELETE 恢复默认
    deleted = client.http.delete(f"{BASE}/prompts/{node_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["has_override"] is False
    # 幂等 DELETE
    assert client.http.delete(f"{BASE}/prompts/{node_id}").status_code == 200


def test_upsert_with_spaces_in_node_id_roundtrip(client):
    """node_id 含空格（encode 后路径参数往返，前端 encodeURIComponent 同语义）。"""
    node_id = "market:International Event Extraction Analyst"
    put = client.http.put(
        f"{BASE}/prompts/{node_id.replace(' ', '%20')}", json={"prompt_text": "覆盖"})
    assert put.status_code == 200, put.text
    assert put.json()["data"]["node_id"] == node_id
    client.http.delete(f"{BASE}/prompts/{node_id.replace(' ', '%20')}")


def test_screening_422_and_unknown_404(client):
    screening = client.http.put(f"{BASE}/prompts/screening:Screening", json={"prompt_text": "x"})
    assert screening.status_code == 422
    assert screening.json()["code"] == "AGENT_PROMPT_NOT_EDITABLE"
    unknown = client.http.put(f"{BASE}/prompts/market:不存在节点", json={"prompt_text": "x"})
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "AGENT_NODE_NOT_FOUND"


def test_empty_and_too_long_422(client):
    empty = client.http.put(f"{BASE}/prompts/market:CN News Analyst", json={"prompt_text": "  "})
    assert empty.status_code == 422
    assert empty.json()["code"] == "VALIDATION_ERROR"
    too_long = client.http.put(
        f"{BASE}/prompts/market:CN News Analyst", json={"prompt_text": "长" * 20001})
    assert too_long.status_code == 422
    assert too_long.json()["code"] == "VALIDATION_ERROR"
