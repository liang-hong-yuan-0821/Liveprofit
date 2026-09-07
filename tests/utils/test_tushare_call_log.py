"""
tushare 端点子日志测试（AI/utils/dataprovider_log.py 的 wrap_tushare_api）：

  {dp调用目录}/tushare/{seq:03d}_{api_name}/req.json + res.json + meta.json

- wrap_tushare_api 接受任意带 .query 方法的对象，无需安装 tushare
- 仅在 @dataprovider_log 装饰函数内（DP 调用目录已创建）落盘；run 外透传
- 端点异常：写 res.json {"error"} 后原样 re-raise；DF 结果归一 + 超行截断

注意：_run 是全局单例，用例通过 fixture 内 reset() 隔离状态。
"""

import functools
import json
import threading
import time
from functools import partial
from pathlib import Path

import pandas as pd
import pytest

from AI.utils.dataprovider_log import (
    _TUSHARE_RES_MAX_ROWS,
    _current_node,
    _current_dp_call,
    dataprovider_log,
    wrap_tushare_api,
)
from AI.utils.llm_callbacks import _run
from AI.dataflows.providers.cn.tushare import TushareProvider


@pytest.fixture
def dp_log_env(tmp_path):
    """初始化日志目录 + 节点上下文（隔离全局单例 _run 与 contextvar）"""
    _run.reset(tmp_path)
    token = _current_node.set("International Event Extraction Analyst")
    yield tmp_path
    _current_node.reset(token)


def _agent_dir(tmp_path) -> Path:
    return Path(tmp_path) / "market" / "001_International_Event_Extraction_Analyst"


class _FakeApi:
    """伪 tushare DataApi：query 是唯一出口，可注入返回/异常"""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def query(self, api_name, fields="", **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class _DataApiLike(_FakeApi):
    """模拟真实 tushare DataApi 的属性访问：__getattr__ 对任意未知属性
    返回 partial(self.query, name)（如 self.api.daily 的调用形态）。

    回归点：wrap 的幂等/探测标志判定必须查实例 __dict__，否则会拿到
    truthy 的 partial 误判"已包装"（2026-08-25 真实 DataApi 踩坑）。
    """

    def __getattr__(self, name):
        return functools.partial(self.query, name)


def _df(rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [f"202608{i:02d}" for i in range(1, rows + 1)],
        "close": [1.5 + i for i in range(rows)],
    })


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# ---- 基本落盘链路 ----

def test_endpoint_call_logged_under_dp_dir(dp_log_env):
    """装饰函数内调端点 → DP 调用目录下 tushare/001_{api}/ 落盘"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(2)))

    @dataprovider_log
    def fake_get_stock(ticker: str) -> str:
        """获取行情"""
        return api.query("daily", ts_code=ticker)["close"].to_string()

    fake_get_stock("000001.SH")

    tdir = _agent_dir(tmp_path) / "001_fake_get_stock" / "tushare" / "001_daily"
    assert tdir.is_dir()
    req = _read(tdir / "req.json")
    assert req == {"api_name": "daily", "fields": "", "params": {"ts_code": "000001.SH"}}
    res = _read(tdir / "res.json")
    assert res["columns"] == ["trade_date", "close"]
    assert res["shape"] == [2, 2]
    assert res["row_count"] == 2
    assert res["truncated"] is False
    assert res["records"][0] == {"trade_date": "20260801", "close": 1.5}
    meta = _read(tdir / "meta.json")
    assert meta["name"] == "daily"
    assert meta["seq"] == 1
    assert meta["res"] == "res.json"
    assert meta["probe"] is False


def test_context_not_leaked_outside_dp_call(dp_log_env):
    """装饰函数执行结束后 ctx 恢复为 None，之后的端点调用不落盘"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))

    @dataprovider_log
    def fake_get() -> str:
        """获取数据"""
        return api.query("daily")["close"].to_string()

    fake_get()
    assert _current_dp_call.get() is None

    # run 外直调：透传结果，不产生新目录
    api.query("daily")
    tdir = _agent_dir(tmp_path) / "001_fake_get" / "tushare"
    assert len(list(tdir.iterdir())) == 1  # 仅 001_daily，无 002_


def test_no_ctx_passthrough_no_dir(tmp_path):
    """无 DP 调用上下文（run 外直调）→ 透传结果，不落盘"""
    _run.reset(tmp_path)
    api = wrap_tushare_api(_FakeApi(result=_df(1)))
    df = api.query("daily", ts_code="x")
    assert df.shape == (1, 2)
    assert not list(tmp_path.rglob("tushare"))


# ---- 结果归一 ----

def test_nan_and_empty_dataframe_normalized(dp_log_env):
    """NaN → null；空 DataFrame → shape [0,0] + records []（不产生坏 JSON）"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))
    api.result.loc[0, "close"] = float("nan")

    @dataprovider_log
    def fake_nan() -> str:
        """NaN 数据"""
        return api.query("daily").to_string()

    fake_nan()
    res = _read(_agent_dir(tmp_path) / "001_fake_nan" / "tushare" / "001_daily" / "res.json")
    assert res["records"][0]["close"] is None  # to_json 把 NaN 归一为 null

    empty = wrap_tushare_api(_FakeApi(result=pd.DataFrame()))

    @dataprovider_log
    def fake_empty() -> str:
        """空数据"""
        return empty.query("daily").to_string()

    fake_empty()
    res2 = _read(_agent_dir(tmp_path) / "002_fake_empty" / "tushare" / "001_daily" / "res.json")
    assert res2["columns"] == []
    assert res2["shape"] == [0, 0]
    assert res2["records"] == []
    assert res2["row_count"] == 0


def test_truncation_over_max_rows(dp_log_env):
    """records 超上限截断：truncated=true + row_count 保留真实行数"""
    tmp_path = dp_log_env
    rows = _TUSHARE_RES_MAX_ROWS + 100
    api = wrap_tushare_api(_FakeApi(result=_df(rows)))

    @dataprovider_log
    def fake_big() -> str:
        """大结果"""
        return api.query("daily").to_string()

    fake_big()
    res = _read(_agent_dir(tmp_path) / "001_fake_big" / "tushare" / "001_daily" / "res.json")
    assert res["truncated"] is True
    assert res["row_count"] == rows
    assert len(res["records"]) == _TUSHARE_RES_MAX_ROWS


# ---- 异常路径 ----

def test_endpoint_exception_logs_error_and_reraise(dp_log_env):
    """端点抛异常（如 code!=0）→ res.json {"error"} + meta.error=true，原异常上抛"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(error=Exception("权限不足")))

    @dataprovider_log
    def fake_moneyflow() -> str:
        """资金流"""
        return api.query("moneyflow").to_string()

    with pytest.raises(Exception, match="权限不足"):
        fake_moneyflow()

    tdir = _agent_dir(tmp_path) / "001_fake_moneyflow" / "tushare" / "001_moneyflow"
    res = _read(tdir / "res.json")
    assert res == {"error": "Exception: 权限不足", "api_name": "moneyflow"}
    assert _read(tdir / "meta.json")["error"] is True


def test_dp_fn_exception_keeps_dir_with_error(dp_log_env):
    """DP 函数抛异常：目录保留 req.json + res.json({"error"})，异常上抛且不挂检查点"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))

    @dataprovider_log
    def fake_fail() -> str:
        """会失败"""
        api.query("daily")
        raise ValueError("组装失败")

    with pytest.raises(ValueError, match="组装失败"):
        fake_fail()

    d = _agent_dir(tmp_path) / "001_fake_fail"
    assert (d / "req.json").exists()
    res = _read(d / "res.json")
    assert res == {"error": "ValueError: 组装失败"}
    assert _read(d / "meta.json")["error"] is True
    # 检查点文件不产生（与 test_step_gate 的"仅成功路径"语义一致）
    from AI.utils import step_gate
    assert not (tmp_path / step_gate.CHECKPOINT_FILE).exists()


# ---- 序号与标志 ----

def test_seq_counts_calls_within_dp_call(dp_log_env):
    """同一 DP 调用内多次端点调用 → 001_/002_ 递增"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))

    @dataprovider_log
    def fake_two() -> str:
        """两次调用"""
        api.query("daily")
        return api.query("sw_daily").to_string()

    fake_two()
    tdir = _agent_dir(tmp_path) / "001_fake_two" / "tushare"
    assert (tdir / "001_daily").is_dir()
    assert (tdir / "002_sw_daily").is_dir()
    assert _read(tdir / "002_sw_daily" / "meta.json")["seq"] == 2


def test_seq_resets_per_dp_call(dp_log_env):
    """tushare 序号按 DP 调用独立计数：两个 DP 调用各含 001_daily"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))

    @dataprovider_log
    def fake_a() -> str:
        """A"""
        return api.query("daily").to_string()

    @dataprovider_log
    def fake_b() -> str:
        """B"""
        return api.query("daily").to_string()

    fake_a()
    fake_b()
    agent = _agent_dir(tmp_path)
    assert (agent / "001_fake_a" / "tushare" / "001_daily").is_dir()
    assert (agent / "002_fake_b" / "tushare" / "001_daily").is_dir()


def test_probe_flag_sets_meta_probe_once(dp_log_env):
    """api._lp_probe_next 一次性消费：首次调用 meta.probe=true，后续为 false"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result=_df(1)))
    api._lp_probe_next = True

    @dataprovider_log
    def fake_probe() -> str:
        """探测+正常"""
        api.query("stock_basic")
        return api.query("daily").to_string()

    fake_probe()
    tdir = _agent_dir(tmp_path) / "001_fake_probe" / "tushare"
    assert _read(tdir / "001_stock_basic" / "meta.json")["probe"] is True
    assert _read(tdir / "002_daily" / "meta.json")["probe"] is False
    assert not hasattr(api, "_lp_probe_next")  # 标志已消费


def test_wrap_idempotent(dp_log_env):
    """同一实例重复包装 → 只记一份日志"""
    tmp_path = dp_log_env
    api = _FakeApi(result=_df(1))
    wrap_tushare_api(api)
    wrap_tushare_api(api)

    @dataprovider_log
    def fake_once() -> str:
        """一次"""
        return api.query("daily").to_string()

    fake_once()
    tdir = _agent_dir(tmp_path) / "001_fake_once" / "tushare"
    assert (tdir / "001_daily").is_dir()
    assert not (tdir / "002_daily").exists()


def test_env_disable_skips_wrapping(dp_log_env, monkeypatch):
    """LIVEPROFIT_TUSHARE_LOG=0：wrap 原样返回，不落盘"""
    tmp_path = dp_log_env
    import AI.utils.dataprovider_log as dpl
    monkeypatch.setattr(dpl, "_TUSHARE_LOG_ENABLED", False)
    api = _FakeApi(result=_df(1))
    wrapped = dpl.wrap_tushare_api(api)
    assert wrapped is api

    @dataprovider_log
    def fake_off() -> str:
        """关闭"""
        return api.query("daily").to_string()

    fake_off()
    assert not (_agent_dir(tmp_path) / "001_fake_off" / "tushare").exists()


def test_non_dataframe_result_passthrough(dp_log_env):
    """非 DataFrame 结果（dict/list）原样写入 res.json"""
    tmp_path = dp_log_env
    api = wrap_tushare_api(_FakeApi(result={"code": 0, "msg": "ok"}))

    @dataprovider_log
    def fake_dict() -> str:
        """dict"""
        return str(api.query("some_api"))

    fake_dict()
    res = _read(_agent_dir(tmp_path) / "001_fake_dict" / "tushare" / "001_some_api" / "res.json")
    assert res == {"code": 0, "msg": "ok"}


def test_timeout_late_write_attribution(dp_log_env):
    """_run_with_timeout 显式复制 contextvars 进 worker（2026-08-25 踩坑回归）：

    实测 Py3.12 ThreadPoolExecutor 不自动传播 contextvars——若无 copy_context，
    worker 内读到 None 不落盘。超时返回 None 后 worker 补写日志，归属仍为
    当前 DP 调用目录（「DP res 超时、tushare 有记录」并存的预期诊断行为）。
    """
    tmp_path = dp_log_env
    gate = threading.Event()

    class BlockingApi(_FakeApi):
        def query(self, api_name, fields="", **kwargs):
            gate.wait(timeout=10)
            return super().query(api_name, fields, **kwargs)

    api = wrap_tushare_api(BlockingApi(result=_df(1)))
    # 按 tests/dataflows 的假实例模式构造 provider，走 _api_call（超时 → None）
    prov = TushareProvider.__new__(TushareProvider)
    prov.api = api

    @dataprovider_log
    def fake_slow() -> str:
        """慢接口"""
        return str(prov._api_call(partial(api.query, "daily"), timeout=0.3))

    assert fake_slow() == "None"  # 超时 → None，不抛异常

    tdir = _agent_dir(tmp_path) / "001_fake_slow" / "tushare" / "001_daily"
    assert not tdir.exists()  # worker 未完成，尚未落盘

    gate.set()  # 放行 worker → 补写 tushare 日志
    deadline = time.monotonic() + 5
    while not tdir.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert tdir.is_dir()
    meta = _read(tdir / "meta.json")
    assert meta["name"] == "daily"
    assert meta["seq"] == 1


def test_dataapi_like_getattr_partial(dp_log_env):
    """真实 DataApi 形态（__getattr__ → partial）：属性访问式调用正常落盘、
    幂等判定不受 partial 干扰、未设探测标志不误判 probe"""
    tmp_path = dp_log_env
    api = _DataApiLike(result=_df(2))
    assert wrap_tushare_api(api) is api
    assert wrap_tushare_api(api) is api  # 幂等：__getattr__ 的 partial 不干扰判定

    @dataprovider_log
    def fake_attr() -> str:
        """属性访问式"""
        return api.daily(ts_code="000001.SH").to_string()  # __getattr__ → partial(query, "daily")

    fake_attr()
    tdir = _agent_dir(tmp_path) / "001_fake_attr" / "tushare" / "001_daily"
    assert tdir.is_dir()
    meta = _read(tdir / "meta.json")
    assert meta["probe"] is False  # 未设 _lp_probe_next → 不误判（__dict__ 判定）
    assert api.calls == 1  # 双重 wrap 未叠加调用
