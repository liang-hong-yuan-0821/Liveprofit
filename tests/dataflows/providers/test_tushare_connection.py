"""单元测试：TushareProvider 连接初始化与懒重连（2026-09-21 修复）。

背景实况：ts.set_token 无条件写家目录 ~/tk.csv，写入被拒（Permission denied）
→ _connect 整体失败 → 失败态被 provider 单例永久缓存，个股因子按需拉取长期
返回 None（指标静默降级纯 K 线）。修复两件事：
1. token 直传 ts.pro_api(token)，不再落盘 tk.csv（根因）；
2. connected 读取懒重连（冷却期），瞬态失败后自动恢复、不永久缓存失败态。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from AI.dataflows.providers.cn.tushare import TushareProvider


def _bare_provider() -> TushareProvider:
    """__new__ 绕过 __init__（与既有 provider 测试同款直注模式）。"""
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.api = None
    prov._connected = False
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)  # 直通（不经过线程池）
    return prov


def _patch_connect(monkeypatch, api: MagicMock):
    """注入假 ts（pro_api 直传 token、set_token 记调用）与恒真探测。"""
    import AI.dataflows.providers.cn.tushare as mod

    api.stock_basic.return_value = pd.DataFrame({"ts_code": ["000001.SH"]})
    fake_ts = SimpleNamespace(
        pro_api=lambda token="", timeout=30: api,
        set_token=MagicMock(),
    )
    monkeypatch.setattr(mod, "ts", fake_ts)
    monkeypatch.setattr(mod, "wrap_tushare_api", lambda api: api)
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token")
    return fake_ts


def test_connect_passes_token_directly_without_writing_tk_csv(monkeypatch):
    """token 直传 pro_api（不再调 set_token 落盘 ~/tk.csv），探测成功 → connected。"""
    api = MagicMock()
    fake_ts = _patch_connect(monkeypatch, api)
    prov = _bare_provider()

    prov._connect()

    assert prov._connected is True
    fake_ts.set_token.assert_not_called()  # 根因修复：不再写家目录 tk.csv
    assert prov.api is api


def test_connected_lazy_reconnects_after_failed_connect(monkeypatch):
    """懒重连：冷却期已过 → 读取 connected 自动重连；冷却期内 → 不重试。"""
    import time as time_mod

    import AI.dataflows.providers.cn.tushare as mod

    api = MagicMock()
    api.stock_basic.return_value = pd.DataFrame({"ts_code": ["000001.SH"]})  # 探测非空 → 连接成功
    pro_api_calls: list[int] = []
    fake_ts = SimpleNamespace(pro_api=lambda token="", timeout=30: (pro_api_calls.append(1) or api))
    monkeypatch.setattr(mod, "ts", fake_ts)
    monkeypatch.setattr(mod, "wrap_tushare_api", lambda api: api)
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-token")

    prov = _bare_provider()
    prov._lazy_reconnect = True
    prov._last_connect_attempt = time_mod.monotonic() - TushareProvider.CONNECT_RETRY_COOLDOWN_SECONDS - 1

    assert prov.connected is True  # 失败态已过冷却期 → 读取即重连成功
    assert len(pro_api_calls) == 1

    # 冷却期内再断开（模拟瞬态失败）→ 读取不触发重连、不连打探测
    prov._connected = False
    prov._last_connect_attempt = time_mod.monotonic()
    assert prov.connected is False
    assert len(pro_api_calls) == 1


def test_new_instance_without_init_keeps_disconnected_semantics():
    """__new__ 直注实例默认不启用懒重连：connected=False 读取不触发 _connect
    （既有「未连接 → 直接 None」测试语义的回归锚）。"""
    prov = _bare_provider()
    prov.connected = False
    assert prov.connected is False
