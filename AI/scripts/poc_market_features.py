"""市场层四接口 Tushare 代理端点实测 POC（任务清单 T1 / 方案第十三章步骤 2）。

探测目标（对应方案第十二章 Provider 接口表）：
  1. get_market_valuation        估值分位   —— index_dailybasic / daily_basic
  2. get_cn_liquidity_indicators 利率流动性 —— yield_curve / shibor / shibor_lpr / cn_m 等
  3. get_global_risk_indicators  海外风险价格 —— index_global / us_tycr / fx_daily 等
  4. get_margin_trading_history  两融历史   —— margin / margin_detail

约束：
- 使用代理端点 https://ts.gyzcloud.top/api（_DataApi__http_url name-mangled 写法）
- token 来自 .env 的 TUSHARE_TOKEN（LIVEPROFIT_DATA_SOURCE=tushare）
- 全市场拉取只做单日 trade_date 查询；区间查询仅作"是否截断"对照证据
- 输出统一 UTF-8（Windows GBK locale）

用法：
    .venv/Scripts/python.exe AI/scripts/poc_market_features.py
"""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧环境忽略
        pass

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

try:
    import pandas as pd
    import tushare as ts
except Exception as exc:  # noqa: BLE001
    print(f"[FATAL] tushare/pandas 导入失败: {exc}")
    sys.exit(2)

PROXY_URL = "https://ts.gyzcloud.top/api"
CALL_TIMEOUT = 60  # 单次端点调用超时（秒）

RESULTS: list[dict] = []  # 全部探测记录


# ---------------------------------------------------------------- 基础设施


def _run_with_timeout(fn, timeout, *args, **kwargs):
    """线程池超时保护（对齐 providers/cn/tushare.py 的 _run_with_timeout）。"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def _classify_error(msg: str) -> str:
    if "没有接口访问权限" in msg or "没有访问该接口的权限" in msg or "权限" in msg:
        return "NO_PERMISSION"
    if "积分" in msg:
        return "NO_PERMISSION"
    if "不存在" in msg or "无此接口" in msg or "请确认接口" in msg:
        return "NOT_FOUND"
    if "频率" in msg or "超过" in msg:
        return "RATE_LIMIT"
    return "ERROR"


def connect():
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        print("[FATAL] .env 未配置有效 TUSHARE_TOKEN")
        sys.exit(2)
    ts.set_token(token)
    api = ts.pro_api()
    api._DataApi__http_url = PROXY_URL  # 代理端点（name-mangled 私有属性）
    return api


API = connect()


def probe(endpoint: str, label: str, quiet: bool = False, **kwargs):
    """调用一个端点并记录结构化探测结果；返回 DataFrame 或 None。"""
    call_kwargs = {k: v for k, v in kwargs.items() if v is not None}
    rec = {
        "endpoint": endpoint,
        "label": label,
        "kwargs": {k: str(v) for k, v in call_kwargs.items()},
    }
    t0 = time.time()
    df = None
    try:
        fn = getattr(API, endpoint)  # 未知属性返回 partial(query, name)，调用时才报错
        df = _run_with_timeout(lambda: fn(**call_kwargs), CALL_TIMEOUT)
        rec["status"] = "OK" if df is not None else "NONE"
        if df is not None and len(df) > 0:
            rec["rows"] = len(df)
            rec["cols"] = list(df.columns)
            dates = None
            for col in ("trade_date", "date", "cal_date", "end_date"):
                if col in df.columns:
                    dates = df[col].astype(str)
                    break
            if dates is not None:
                rec["date_min"], rec["date_max"] = dates.min(), dates.max()
        elif df is not None:
            rec["rows"] = 0
    except FutureTimeout:
        rec["status"] = "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        msg = " ".join(str(exc).split())
        rec["status"] = _classify_error(msg)
        rec["error"] = msg[:220]
    rec["sec"] = round(time.time() - t0, 1)
    RESULTS.append(rec)
    if not quiet:
        _print_rec(rec, df)
    return df


def _print_rec(rec: dict, df):
    status = rec["status"]
    head = f"[{status:>13}] {rec['endpoint']:<16} {rec['label']}"
    detail = ""
    if status in ("OK", "NONE"):
        detail = f" rows={rec.get('rows', 0)}"
        if rec.get("date_min"):
            detail += f" span={rec['date_min']}..{rec['date_max']}"
        if rec.get("cols"):
            detail += f" cols={','.join(rec['cols'])}"
    else:
        detail = f" {rec.get('error', '')}"
    print(f"{head}{detail}  ({rec['sec']}s)")
    if df is not None and len(df) > 0:
        with pd.option_context("display.max_columns", 40, "display.width", 200):
            print("    sample:")
            for line in df.head(2).to_string(index=False).splitlines():
                print(f"      {line}")


def banner(title: str):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def trade_days(n: int):
    """最近 n 个交易日（升序），用 trade_cal 取。"""
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - __import__("datetime").timedelta(days=40)).strftime("%Y%m%d")
    df = probe("trade_cal", f"最近 {n} 个交易日", quiet=True,
               exchange="SSE", start_date=start, end_date=today, is_open="1")
    if df is None or df.empty:
        return []
    days = sorted(df["cal_date"].astype(str).tolist())
    return days[-n:]


def fmt_val(v, digits=2) -> str:
    try:
        f = float(v)
        return f"{f:.{digits}f}"
    except Exception:  # noqa: BLE001
        return str(v)


def code_list(df, col: str = "ts_code"):
    """打印 DataFrame 中某列全部去重取值（用于枚举代理端点实际代码集）。"""
    if df is None or df.empty or col not in df.columns:
        return []
    vals = sorted({str(v) for v in df[col].tolist()})
    print(f"    代码集({len(vals)}): {', '.join(vals)}")
    return vals


# ---------------------------------------------------------------- 0. 连通性

banner("0. 连通性与交易日基准")
probe("stock_basic", "连通性探测 (limit=1)", list_status="L", limit=1)
DAYS = trade_days(6)
print(f"最近交易日（升序）: {DAYS}")
DAY = DAYS[-1] if DAYS else ""
PREV_DAY = DAYS[-2] if len(DAYS) >= 2 else DAY
DAY_5Y_AGO = (datetime.strptime(DAY, "%Y%m%d") - __import__("datetime").timedelta(days=365 * 5)).strftime("%Y%m%d")
print(f"基准日: DAY={DAY} PREV_DAY={PREV_DAY} 5Y_AGO={DAY_5Y_AGO}")


# ---------------------------------------------------------------- 1. 估值分位

banner("1. get_market_valuation 估值分位 —— index_dailybasic / daily_basic")

idx_day = probe("index_dailybasic", f"指数估值单日全量 trade_date={DAY}", trade_date=DAY)
if idx_day is None or len(idx_day) == 0:
    idx_day = probe("index_dailybasic", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY)
code_list(idx_day)

IDX_CODE = "000300.SH"
idx_5y = probe("index_dailybasic", f"{IDX_CODE} 5 年区间", ts_code=IDX_CODE,
               start_date=DAY_5Y_AGO, end_date=DAY)
probe("index_dailybasic", f"{IDX_CODE} 10 年区间（深度上限探测）", ts_code=IDX_CODE,
      start_date=(datetime.strptime(DAY, "%Y%m%d") - __import__("datetime").timedelta(days=365 * 10)).strftime("%Y%m%d"),
      end_date=DAY)
probe("index_dailybasic", f"{IDX_CODE} 单日", ts_code=IDX_CODE, trade_date=DAY)

db_day = probe("daily_basic", f"个股估值单日全市场 trade_date={DAY}", trade_date=DAY)
probe("daily_basic", f"区间对照（无 ts_code，5 年）截断证据", start_date=DAY_5Y_AGO, end_date=DAY)
probe("daily_basic", "单股 5 年区间深度", ts_code="600000.SH",
      start_date=DAY_5Y_AGO, end_date=DAY)

# 5 年分位可算性演示（用指数区间序列）
print("\n[结论依据] 5 年分位可算性演示：")
for col in ("pe_ttm", "pb"):
    if idx_5y is not None and len(idx_5y) >= 2 and col in idx_5y.columns:
        series = pd.to_numeric(idx_5y[col], errors="coerce").dropna()
        if len(series) >= 2:
            latest = series.iloc[0]  # 降序返回时首行为最新
            pct = float((series < latest).sum()) / len(series) * 100
            print(f"  {IDX_CODE} {col}: 样本={len(series)} 最新={fmt_val(latest)} "
                  f"5年分位={pct:.1f}%")
    else:
        print(f"  {IDX_CODE} {col}: 不可算（无序列或无该字段）")

if db_day is not None and len(db_day) > 0 and "pe_ttm" in db_day.columns:
    vals = pd.to_numeric(db_day["pe_ttm"], errors="coerce").dropna()
    print(f"  全 A 单日 pe_ttm: 样本={len(vals)} 中位数={fmt_val(vals.median())} "
          f"（全 A 分位需逐日单日拉取 5 年 ≈ 1225 次调用）")


# ---------------------------------------------------------------- 2. 利率流动性

banner("2. get_cn_liquidity_indicators 利率与流动性")

print("[10Y 国债收益率] 端点名对照（现有 get_macro_context 用 yield_curve）：")
yc_day = probe("yield_curve", f"中债收益率曲线单日 trade_date={DAY} curve_type=0",
               trade_date=DAY, curve_type="0")
if yc_day is None or len(yc_day) == 0:
    probe("yield_curve", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY, curve_type="0")
probe("yield_curve", "区间查询（5 年）深度", curve_type="0",
      start_date=DAY_5Y_AGO, end_date=DAY)
for code, label in [("1", "国债"), ("0", "中短期票据(AAA)"), ("2", "国开")]:
    probe("yield_curve", f"curve_type={code} {label} 单日", trade_date=DAY, curve_type=code,
          quiet=(code != "0"))

yc_cb_day = probe("yc_cb", f"中债收益率曲线(别名 yc_cb) 单日 trade_date={DAY} curve_type=0",
                  trade_date=DAY, curve_type="0")
if yc_cb_day is None or len(yc_cb_day) == 0:
    probe("yc_cb", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY, curve_type="0")
yc_cb_5y = probe("yc_cb", "中债收益率曲线 5 年区间深度", curve_type="0",
                 start_date=DAY_5Y_AGO, end_date=DAY)
for code, label in [("1", "国债"), ("2", "国开"), ("0", "中短期票据(AAA)")]:
    probe("yc_cb", f"curve_type={code} {label} 单日", trade_date=DAY, curve_type=code,
          quiet=(code != "0"))
if yc_cb_day is not None and len(yc_cb_day) > 0:
    print("    [10Y 提取演示]")
    print("      " + yc_cb_day.head(12).to_string(index=False).replace("\n", "\n      "))

shibor = probe("shibor", "Shibor 区间（近 30 日）",
               start_date=(datetime.now() - __import__("datetime").timedelta(days=30)).strftime("%Y%m%d"),
               end_date=DAY)
probe("shibor", f"Shibor 单日 date={DAY}（参数对照）", date=DAY)
probe("shibor", f"Shibor 同日 start=end={DAY}（正确参数用法）",
      start_date=DAY, end_date=DAY)
probe("shibor_quote", f"Shibor 报价单日 trade_date={DAY}", trade_date=DAY)

print("\n[LPR] 端点与时效性：")
probe("shibor_lpr", f"LPR 单日 date={DAY}（参数对照）", date=DAY)
probe("shibor_lpr", f"LPR 同日 start=end={DAY}（正确参数用法）", start_date=DAY, end_date=DAY)
probe("shibor_lpr", "LPR 区间（5 年）深度", start_date=DAY_5Y_AGO, end_date=DAY)
probe("shibor_lpr", "LPR 最近 3 个月（时效性核对，最新应为 20260820/20260920）",
      start_date="20260601", end_date=DAY)
probe("lpr", f"lpr 端点别名探测 date={DAY}", date=DAY, quiet=True)
probe("lpr_1y", "lpr_1y 端点探测", quiet=True)
probe("lpr_5y", "lpr_5y 端点探测", quiet=True)

print("\n[DR007 候选端点探测]：")
for name, kw in [
    ("dr007", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("dr007_rate", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("interbank_repo", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("wz_index", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("repo_daily", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
]:
    probe(name, f"候选 {name} {kw}", **kw)
repo_range = None  # repo_daily 区间查询已在上面探测（实测 30s 读超时）

print("\n[10Y 补充候选 / Shibor 报价参数对照]：")
for name, kw in [
    ("cn_yield_curve", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("bond_yield_curve", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("yield_cb", {"trade_date": DAY, "curve_type": "0"}),
    ("shibor_quote", {"start_date": DAY, "end_date": DAY}),
    ("shibor_quote", {"date": DAY}),
    ("cn_10y", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
]:
    probe(name, f"候选 {name} {kw}", **kw)

print("\n[候选端点探测] 净投放 / 货币市场 / MLF / 公开市场操作：")
for name, kw in [
    ("cn_m", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("cn_pbc_omo", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("pbc_omo", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("open_market_operation", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("cn_omo_operation", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("mlf", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("mlf_rate", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("cn_shibor", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("cn_m", {"date": DAY}),
    ("money_supply", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("sf_month", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("repo_daily", {"trade_date": DAY}),
    ("money_market", {"trade_date": DAY}),
    ("mbox", {"trade_date": DAY}),
    ("cn_omo", {"trade_date": DAY}),
    ("open_market", {"trade_date": DAY}),
    ("omo", {"trade_date": DAY}),
    ("cn_gdp", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("cn_cpi", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
]:
    probe(name, f"候选 {name} {kw}", **kw)


# ---------------------------------------------------------------- 3. 海外风险价格

banner("3. get_global_risk_indicators 海外风险价格 —— DXY / 美债 / VIX")

ig_day = probe("index_global", f"国际指数单日全量 trade_date={DAY}", trade_date=DAY)
if ig_day is None or len(ig_day) == 0:
    ig_day = probe("index_global", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY)
code_list(ig_day)
union_codes: set[str] = set()
for probe_day in DAYS[-4:]:
    d = probe("index_global", f"代码全域探测 trade_date={probe_day}", trade_date=probe_day, quiet=True)
    if d is not None and "ts_code" in d.columns:
        union_codes |= {str(v) for v in d["ts_code"].tolist()}
print(f"    近 4 交易日 index_global 代码并集({len(union_codes)}): {', '.join(sorted(union_codes))}")

print("\n[index_global 候选代码 5 年区间扫描] VIX / SOX / 美元指数代理：")
for code in ("SPX", "IXIC", "DJI", "VIX", "SOX", "UDI", "USDX", "DXY", "RUT",
             "FTSE", "GDAXI", "FCHI", "AS51", "SENSEX", "N225", "KS11", "STI",
             "XIN9", "HKAH", "HKTECH", "TWII", "CKLSE"):
    probe("index_global", f"index_global ts_code={code} 近 5 年", ts_code=code,
          start_date=DAY_5Y_AGO, end_date=DAY, quiet=True)
    rec = RESULTS[-1]
    print(f"  [index_global {code:<8}] {rec['status']:<13} rows={rec.get('rows', 0):<6} "
          f"span={rec.get('date_min', '-')}..{rec.get('date_max', '-')}")

probe("us_tycr", f"美国国债收益率曲线单日 trade_date={DAY}", trade_date=DAY)
probe("us_tycr", "美国国债收益率曲线近 5 年", start_date=DAY_5Y_AGO, end_date=DAY)
probe("us_trycr", "美国国债实际收益率近 5 年", start_date=DAY_5Y_AGO, end_date=DAY)
probe("us_trltr", "美国国债长期利率近 5 年", start_date=DAY_5Y_AGO, end_date=DAY)

print("\n[候选端点探测] 美元指数 / 汇率 / 商品：")
probe("fx_daily", f"fx_daily 单日 trade_date={DAY}", trade_date=DAY)
probe("fx_daily", "fx_daily 近 5 年区间（无 ts_code）", start_date=DAY_5Y_AGO, end_date=DAY)
fx_ob = probe("fx_obasic", "fx_obasic 外汇基础信息（代码集）", limit=100)
code_list(fx_ob)
for code in ("USDX.FX", "DXY.FX", "USDCNH.FX", "USDCNY.FX",
             "USDOLLAR.FXCM", "USDCNH.FXCM", "EURUSD.FXCM", "XAUUSD.FXCM",
             "Copper.FXCM", "USOil.FXCM", "NGAS.FXCM"):
    probe("fx_daily", f"fx_daily ts_code={code} 近 5 年", ts_code=code,
          start_date=DAY_5Y_AGO, end_date=DAY, quiet=True)
    rec = RESULTS[-1]
    print(f"  [fx_daily {code}] {rec['status']} rows={rec.get('rows', 0)} "
          f"span={rec.get('date_min', '-')}..{rec.get('date_max', '-')}")
fut = probe("fut_daily", f"期货日线单日（商品对照） trade_date={DAY}", trade_date=DAY)
fut_codes = {str(v) for v in fut["ts_code"].tolist()} if fut is not None and "ts_code" in fut.columns else set()
print(f"    期货代码总数={len(fut_codes)}；含 AU.SHF={('AU.SHF' in fut_codes)} CU.SHF={('CU.SHF' in fut_codes)} "
      f"SC.INE={('SC.INE' in fut_codes)} 黄金/原油对照（无 WTI/伦金）")
for code in ("AU.SHF", "SC.INE", "CU.SHF", "AU.SHF2606"):
    probe("fut_daily", f"fut_daily ts_code={code} 近 5 年（商品降级路径）", ts_code=code,
          start_date=DAY_5Y_AGO, end_date=DAY, quiet=True)
    rec = RESULTS[-1]
    print(f"  [fut_daily {code}] {rec['status']} rows={rec.get('rows', 0)} "
          f"span={rec.get('date_min', '-')}..{rec.get('date_max', '-')}")
probe("index_daily", "对照：A 股指数日线 000300.SH 近 5 年（代理基准能力）",
      ts_code="000300.SH", start_date=DAY_5Y_AGO, end_date=DAY)
for name, kw in [
    ("us_daily", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("us_basic", {"limit": 1}),
    ("hibor", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("libor", {"start_date": DAY_5Y_AGO, "end_date": DAY}),
    ("dxy", {"trade_date": DAY}),
    ("vix", {"trade_date": DAY}),
    ("index_us_stock_sina", {"symbol": "VIX"}),
    ("fut_daily", {"trade_date": DAY}),
]:
    probe(name, f"候选 {name} {kw}", **kw)


# ---------------------------------------------------------------- 4. 两融历史

banner("4. get_margin_trading_history 两融历史 —— margin / margin_detail")

m_day = probe("margin", f"两融汇总单日 trade_date={DAY}", trade_date=DAY)
if m_day is None or len(m_day) == 0:
    probe("margin", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY)
m_20 = probe("margin", "两融汇总近 20 交易日（区间）",
             start_date=DAYS[max(0, len(DAYS) - 20)] if DAYS else "",
             end_date=DAY)
probe("margin", "两融汇总 5 年区间（截断证据）", start_date=DAY_5Y_AGO, end_date=DAY)

print("\n[结论依据] margin 历史深度（单日探针）：")
for probe_day in ("20100401", "20120104", "20150105", "20180102", "20210104", "20240102"):
    df = probe("margin", f"margin 单日 depth probe {probe_day}", trade_date=probe_day, quiet=True)
    rec = RESULTS[-1]
    n = rec.get("rows", 0)
    print(f"  {probe_day}: {rec['status']} rows={n}")

md_day = probe("margin_detail", f"两融明细单日全市场 trade_date={DAY}", trade_date=DAY)
if md_day is None or len(md_day) == 0:
    probe("margin_detail", f"回退前一交易日 trade_date={PREV_DAY}", trade_date=PREV_DAY)
probe("margin_detail", "两融明细区间（无 ts_code，截断证据）",
      start_date=DAYS[max(0, len(DAYS) - 20)] if DAYS else "", end_date=DAY)
probe("margin_detail", "单股两融 5 年深度", ts_code="600000.SH",
      start_date=DAY_5Y_AGO, end_date=DAY)
probe("margin_secs", "margin_secs 端点探测", trade_date=DAY)


# ---------------------------------------------------------------- 汇总

banner("探测汇总（endpoint | label | status | rows | span）")
for rec in RESULTS:
    span = f"{rec.get('date_min', '-')}..{rec.get('date_max', '-')}" if rec.get("date_min") else "-"
    print(f"{rec['endpoint']:<16} | {rec['status']:<13} | rows={rec.get('rows', 0):<6} | "
          f"{span:<20} | {rec['label']}")

status_counts: dict[str, int] = {}
for rec in RESULTS:
    status_counts[rec["status"]] = status_counts.get(rec["status"], 0) + 1
print(f"\n状态统计: {status_counts}")
print("\n完成。以上为该代理端点在指定探针下的真实返回。")
