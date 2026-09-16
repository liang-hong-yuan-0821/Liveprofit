"""
AI 预填写模块测试（2026-08-18；2026-09-11 补三级路由建议值；2026-09-16 名称解析与回填）

覆盖：LLM 输出容错解析（JSON/代码块/杂质/坏输出）、字段清洗（值域收敛/
非法类型置 None）、LLM 不可用降级，路由字段（event_scope /
affected_scope_refs）的建议值生成、值域清洗、幻化引用初筛与失败降级，
以及 2026-09-16 新增的名称解析（resolver 接入/unresolved_entities）、
回填谓词 needs_prelabel、force 覆写与 TTL 写回。
"""

import json

import pytest

from AI.eventStudy.collectors.config import KEY_PENDING_EVENT
from AI.eventStudy.review import ai_prelabel
from AI.eventStudy.review.review_dao import MAX_SCOPE_REFS


class FakeExistence:
    """存在性初筛数据源替身（missing 中的引用视为不存在）。"""

    def __init__(self, missing=()):
        self.missing = set(missing)
        self.queried = []

    def missing_refs(self, refs):
        self.queried.extend(refs)
        return [r for r in refs if r in self.missing]


class FakeResolver:
    """名称解析替身：mapping 中的名称 → 引用；其余名称未解析。"""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.queried = []

    def resolve_names(self, names, scope):
        self.queried.extend(names)
        resolved = [self.mapping[n] for n in names if n in self.mapping]
        unresolved = [n for n in names if n not in self.mapping]
        return resolved, unresolved


class BrokenExistence:
    """存在性数据源不可用替身（连接/码表缺失）。"""

    def missing_refs(self, refs):
        raise RuntimeError("码表未迁移")


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.ex_args = {}

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ex_args[key] = ex


def test_parse_plain_json():
    text = '{"event_type": "央行", "event_subtype": "降准", "event_condition": "利好", "importance": 5, "expected_value": null, "actual_value": null, "previous_value": null}'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "央行"


def test_parse_json_code_block():
    text = '```json\n{"event_type": "宏观数据", "importance": 4}\n```'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "宏观数据"


def test_parse_json_with_noise():
    text = '分析如下：\n{"event_type": "地缘", "importance": 4}\n以上仅供参考。'
    assert ai_prelabel._parse_json_output(text)["event_type"] == "地缘"


def test_parse_bad_output_returns_none():
    assert ai_prelabel._parse_json_output("这不是 JSON") is None
    assert ai_prelabel._parse_json_output("") is None
    assert ai_prelabel._parse_json_output(None) is None


def test_sanitize_clamps_and_nullifies():
    # importance 越界收敛到 1-5
    s = ai_prelabel._sanitize({"event_type": "央行", "importance": 99,
                               "expected_value": "abc", "actual_value": "2.1",
                               "previous_value": None, "event_condition": 123})
    assert s["importance"] == 5
    assert s["expected_value"] is None        # 非数字 → None
    assert s["actual_value"] == pytest.approx(2.1)
    assert s["previous_value"] is None
    assert s["event_condition"] == "123"      # 转字符串


def test_prelabel_one_llm_unavailable(monkeypatch):
    """LLM 不可用 → 返回空建议，不阻塞。"""
    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: None)
    assert ai_prelabel.prelabel_one({"title": "测试", "content": ""}) == {}


def test_prelabel_one_bad_output(monkeypatch):
    """LLM 输出不可解析 → 返回空建议。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content": "随便什么文本"})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    assert ai_prelabel.prelabel_one({"title": "测试", "content": ""}) == {}


def test_prelabel_one_success(monkeypatch):
    """正常路径：LLM 输出经解析 + 清洗后返回建议。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content":
                '{"event_type": "央行", "event_subtype": "降准", '
                '"event_condition": "利好", "importance": 5, '
                '"expected_value": null, "actual_value": null, '
                '"previous_value": null}'})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    s = ai_prelabel.prelabel_one({"title": "央行宣布降准", "content": ""})
    assert s["event_type"] == "央行"
    assert s["event_subtype"] == "降准"
    assert s["importance"] == 5
    assert s["actual_value"] is None


# ==================== 路由字段：建议值生成 / 值域清洗 / 幻化拦截 / 降级 ====================

def test_system_prompt_declares_route_fields():
    """提示词声明作用域三值语义、引用语法、中文名称输出与「禁止编造名称与代码」。"""
    prompt = ai_prelabel._SYSTEM_PROMPT
    assert "event_scope" in prompt and "affected_scope_refs" in prompt
    assert "market" in prompt and "sector" in prompt and "stock" in prompt
    assert "禁止编造名称与代码" in prompt
    assert "中文实体名称" in prompt
    assert "不要凭记忆补代码" in prompt
    assert "SW:" in prompt and "CONCEPT:" in prompt and "stock:" in prompt


def test_sanitize_scope_converges_and_cleans_refs():
    """值域清洗：scope 收敛三值，refs 格式归一（非法项进 unresolved_entities）。"""
    s = ai_prelabel._sanitize({
        "event_scope": "Sector",
        "affected_scope_refs": ["801080", "BK1753.DC", "JUNK", "600519"],
    })
    assert s["event_scope"] == "sector"
    assert s["affected_scope_refs"] == ["SW:801080", "CONCEPT:BK1753.DC"]
    assert s["unresolved_entities"] == ["JUNK", "600519"]

    # 非法 scope → None（表单回退 market），不留悬空引用
    s = ai_prelabel._sanitize({"event_scope": "GLOBAL", "affected_scope_refs": ["SW:801080"]})
    assert s["event_scope"] is None
    assert s["affected_scope_refs"] == []

    # market → 目标固定 []
    s = ai_prelabel._sanitize({"event_scope": "market", "affected_scope_refs": ["SW:801080"]})
    assert s["event_scope"] == "market"
    assert s["affected_scope_refs"] == []

    # scope 缺失（LLM 未输出该键）→ None + []
    s = ai_prelabel._sanitize({})
    assert s["event_scope"] is None
    assert s["affected_scope_refs"] == []


# ==================== 名称解析（2026-09-16） ====================

def test_sanitize_resolves_names_via_resolver():
    """名称经 resolver 解析并入 refs，解析顺序 = 名称出现顺序；无未解析名称不写键。"""
    s = ai_prelabel._sanitize(
        {"event_scope": "sector", "affected_scope_refs": ["半导体", "光刻胶"]},
        resolver=FakeResolver({"半导体": "SW:801080", "光刻胶": "CONCEPT:BK1753.DC"}),
    )
    assert s["affected_scope_refs"] == ["SW:801080", "CONCEPT:BK1753.DC"]
    assert "unresolved_entities" not in s


def test_sanitize_mixes_codes_and_names():
    """代码引用在前、解析引用在后，合并去重；纯代码项不进名称解析。"""
    resolver = FakeResolver({"光刻胶": "CONCEPT:BK1753.DC"})
    s = ai_prelabel._sanitize(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "光刻胶", "801080"]},
        resolver=resolver,
    )
    assert s["affected_scope_refs"] == ["SW:801080", "CONCEPT:BK1753.DC"]
    assert "unresolved_entities" not in s
    assert resolver.queried == ["光刻胶"]  # 纯代码项不进解析（方案 4.7.1 ⑦）


def test_sanitize_unresolved_without_resolver():
    """resolver 不可用 → 名称全部进 unresolved_entities（fail-open，不阻塞预填）。"""
    s = ai_prelabel._sanitize(
        {"event_scope": "stock", "affected_scope_refs": ["贵州茅台"]},
    )
    assert s["event_scope"] == "stock"
    assert s["affected_scope_refs"] == []
    assert s["unresolved_entities"] == ["贵州茅台"]


def test_sanitize_partial_resolution():
    """部分名称解析成功：成功并入 refs，失败进 unresolved。"""
    s = ai_prelabel._sanitize(
        {"event_scope": "sector", "affected_scope_refs": ["半导体", "新概念X"]},
        resolver=FakeResolver({"半导体": "SW:801080"}),
    )
    assert s["affected_scope_refs"] == ["SW:801080"]
    assert s["unresolved_entities"] == ["新概念X"]


def test_sanitize_market_scope_drops_names_without_recording():
    """market 作用域：名称不解析不记录（无目标语义）。"""
    s = ai_prelabel._sanitize(
        {"event_scope": "market", "affected_scope_refs": ["半导体"]},
        resolver=FakeResolver({"半导体": "SW:801080"}),
    )
    assert s["event_scope"] == "market"
    assert s["affected_scope_refs"] == []
    assert "unresolved_entities" not in s


def test_sanitize_name_length_and_count_caps():
    """候选名称边界：单条超长丢弃（不进 unresolved）；条数上限 MAX_SCOPE_REFS。"""
    long_name = "长" * 65
    many = [f"名称{i}" for i in range(MAX_SCOPE_REFS + 5)]
    s = ai_prelabel._sanitize(
        {"event_scope": "sector", "affected_scope_refs": [long_name, *many]},
    )
    unresolved = s.get("unresolved_entities") or []
    assert long_name not in unresolved
    assert len(unresolved) == MAX_SCOPE_REFS


def test_sanitize_filters_hallucinated_refs():
    """幻化引用拦截：存在性初筛剔除不存在的代码，保留其余。"""
    existence = FakeExistence(missing=["SW:999999", "CONCEPT:BK9999.DC"])
    s = ai_prelabel._sanitize(
        {"event_scope": "sector",
         "affected_scope_refs": ["801080", "SW:999999", "CONCEPT:BK9999.DC"]},
        existence,
    )
    assert s["affected_scope_refs"] == ["SW:801080"]
    assert set(existence.queried) == {"SW:801080", "SW:999999", "CONCEPT:BK9999.DC"}

    # 全部幻化 → 空引用（表单层按「缺目标阻止提交」提示人工补）
    s = ai_prelabel._sanitize(
        {"event_scope": "stock", "affected_scope_refs": ["600519.SH"]},
        FakeExistence(missing=["stock:600519.SH"]),
    )
    assert s["event_scope"] == "stock"
    assert s["affected_scope_refs"] == []

    # market 不做存在性查询（目标固定 []）
    existence = FakeExistence()
    ai_prelabel._sanitize({"event_scope": "market"}, existence)
    assert existence.queried == []


def test_sanitize_fail_open_when_existence_unavailable():
    """失败降级：存在性数据源不可用 → 保留格式合法引用（服务端严格校验兜底）。"""
    s = ai_prelabel._sanitize(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "600519"]},
        BrokenExistence(),
    )
    assert s["affected_scope_refs"] == ["SW:801080"]


def test_prelabel_one_generates_route_suggestions(monkeypatch):
    """正常路径：LLM 输出的作用域/引用经解析 + 清洗 + 初筛后写回建议。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content": json.dumps({
                "event_type": "产业政策", "event_subtype": "半导体",
                "event_condition": "利好", "importance": 4,
                "expected_value": None, "actual_value": None, "previous_value": None,
                "event_scope": "sector",
                "affected_scope_refs": ["801080", "SW:999999"],
            }, ensure_ascii=False)})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    s = ai_prelabel.prelabel_one(
        {"title": "某行业政策出台", "content": "..."},
        existence=FakeExistence(missing=["SW:999999"]),
    )
    assert s["event_scope"] == "sector"
    assert s["affected_scope_refs"] == ["SW:801080"]


def test_prelabel_one_existence_unavailable_falls_back(monkeypatch):
    """失败降级：存在性数据源异常不阻塞预填（fail-open，仅格式清洗）。"""
    class FakeLLM:
        def invoke(self, messages):
            return type("R", (), {"content":
                '{"event_scope": "stock", "affected_scope_refs": ["600519.SH"]}'})()

    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: FakeLLM())
    s = ai_prelabel.prelabel_one({"title": "公司公告", "content": ""}, existence=BrokenExistence())
    assert s["event_scope"] == "stock"
    assert s["affected_scope_refs"] == ["stock:600519.SH"]


def test_open_existence_unavailable(monkeypatch):
    """PG 不可达 → (None, None, None)，预填继续（不阻塞、不再查询）。"""
    from AI.eventStudy.db import connection as db_connection

    def boom():
        raise RuntimeError("PG 不可达")

    monkeypatch.setattr(db_connection, "get_connection", boom)
    assert ai_prelabel._open_existence() == (None, None, None)


def test_prelabel_events_writes_and_skips_existing(monkeypatch):
    """幂等写回：已有完整建议的草稿跳过；写回带 TTL；连接在批末关闭。"""
    fake_redis = FakeRedis()
    monkeypatch.setattr(ai_prelabel, "is_redis_available", lambda: True)
    monkeypatch.setattr(ai_prelabel, "get_redis_client", lambda: fake_redis)

    class FakeConn:
        closed = False

        def close(self):
            self.closed = True

    conn = FakeConn()
    monkeypatch.setattr(ai_prelabel, "_open_existence",
                        lambda: (FakeExistence(), None, conn))
    monkeypatch.setattr(ai_prelabel, "prelabel_one",
                        lambda draft, existence=None, resolver=None: {
                            "event_scope": "market", "affected_scope_refs": []})

    drafts = [
        {"draft_id": 1, "title": "新草稿"},
        {"draft_id": 2, "title": "已预填",
         "ai_suggestions": {"importance": 3, "event_scope": "market"}},
    ]
    assert ai_prelabel.prelabel_events(drafts) == 1
    key = KEY_PENDING_EVENT.format(draft_id=1)
    assert list(fake_redis.store) == [key]
    assert json.loads(fake_redis.store[key])["ai_suggestions"] == {
        "event_scope": "market", "affected_scope_refs": [],
    }
    assert fake_redis.ex_args[key] == ai_prelabel.PENDING_DRAFT_TTL
    assert conn.closed, "存在性连接应在批末关闭"


def test_prelabel_events_backfills_missing_scope(monkeypatch):
    """回填：缺 event_scope 的旧建议草稿重预填；完整建议草稿跳过。"""
    fake_redis = FakeRedis()
    monkeypatch.setattr(ai_prelabel, "is_redis_available", lambda: True)
    monkeypatch.setattr(ai_prelabel, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(ai_prelabel, "_open_existence", lambda: (None, None, None))
    monkeypatch.setattr(ai_prelabel, "prelabel_one",
                        lambda draft, existence=None, resolver=None: {
                            "event_scope": "market", "affected_scope_refs": []})

    drafts = [
        {"draft_id": 1, "title": "缺 scope 旧建议", "ai_suggestions": {"importance": 3}},
        {"draft_id": 2, "title": "完整建议", "ai_suggestions": {"event_scope": "market"}},
    ]
    assert ai_prelabel.prelabel_events(drafts) == 1
    assert list(fake_redis.store) == [KEY_PENDING_EVENT.format(draft_id=1)]


def test_prelabel_events_force_overwrites_all(monkeypatch):
    """force=True：对传入列表全部重生成（覆写），不做谓词过滤。"""
    fake_redis = FakeRedis()
    monkeypatch.setattr(ai_prelabel, "is_redis_available", lambda: True)
    monkeypatch.setattr(ai_prelabel, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(ai_prelabel, "_open_existence", lambda: (None, None, None))
    monkeypatch.setattr(ai_prelabel, "prelabel_one",
                        lambda draft, existence=None, resolver=None: {
                            "event_scope": "sector", "affected_scope_refs": []})

    drafts = [
        {"draft_id": 1, "title": "t1"},
        {"draft_id": 2, "title": "t2", "ai_suggestions": {"event_scope": "market"}},
    ]
    assert ai_prelabel.prelabel_events(drafts, force=True) == 2
    assert len(fake_redis.store) == 2


def test_needs_prelabel_predicate():
    """回填谓词：无建议 → True；缺 event_scope → True；完整建议 → False。"""
    assert ai_prelabel.needs_prelabel({"draft_id": 1}) is True
    assert ai_prelabel.needs_prelabel(
        {"draft_id": 1, "ai_suggestions": {"importance": 3}}) is True
    assert ai_prelabel.needs_prelabel(
        {"draft_id": 1, "ai_suggestions": {"event_scope": "market"}}) is False
    assert ai_prelabel.needs_prelabel(
        {"draft_id": 1,
         "ai_suggestions": {"event_scope": "stock", "affected_scope_refs": []}}) is False


def test_prelabel_events_llm_unavailable_returns_zero(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(ai_prelabel, "is_redis_available", lambda: True)
    monkeypatch.setattr(ai_prelabel, "get_redis_client", lambda: fake_redis)
    monkeypatch.setattr(ai_prelabel, "_open_existence", lambda: (None, None, None))
    monkeypatch.setattr(ai_prelabel, "get_llm", lambda: None)
    assert ai_prelabel.prelabel_events([{"draft_id": 1, "title": "t"}]) == 0
    assert fake_redis.store == {}


def test_prelabel_events_redis_unavailable_returns_zero(monkeypatch):
    monkeypatch.setattr(ai_prelabel, "is_redis_available", lambda: False)
    assert ai_prelabel.prelabel_events([{"draft_id": 1, "title": "t"}]) == 0
