"""市场特征层：纯函数构建可复现、带时点的市场特征（T5）。

对应方案第四、七、八、十二章：

- 特征函数（第四章）：`build_data_quality_summary` / `build_cn_technical_features`
  / `build_cn_event_calendar_features` / `build_global_risk_features`
- 风险门控（第十二章有序规则表）：`derive_risk_gate`
- 结构化输出解析与降级（第七、八章）：`parse_market_regime`
  / `parse_market_event_calendar`

公共约束（第四章）：
1. 全部特征携带 `as_of_date`；晚于 `trade_date` 的数据丢弃并记录
   `future_data_detected=True`。
2. 趋势特征携带窗口与有效样本数；样本不足返回 `None`。所有 lookbacks/windows
   均为**交易日**口径。
3. 同时保存 `raw_reference`（原始证据引用）与 `derived_metrics`（派生指标）；
   输入为 Provider 结构化返回（DataFrame/dict），Markdown 证据文本由本层生成，
   LLM 不直接消费 Provider 原始输出。
4. 数据不可用、字段变化和解析失败降级为 `partial/insufficient`，不阻断全图。
5. `market_data_quality` 由图启动阶段一次性写入 State，各 Agent 只读，禁止回写。

窗口口径（与验收边界样本一致）：
- 窗口 `N` 使用最近 `N` 个交易日样本；`N-1` 条样本即视为不足并返回 `None`。
- 收益率口径：`close[-1] / close[-N] - 1`（最近 N 个交易日收盘的首尾变化）。
- 斜率口径：最近 `N` 个收盘价对序号做 OLS，输出“归一化斜率%（/交易日）”。

POC 实测约束（T1，`logs/poc_market_features.log`）：
- `daily_basic` 无 `ts_code` 的区间查询会被静默截断（6000 行），必须单日查询；
- `index_dailybasic` 单日 12 个指数代码、`index_daily` 按 `ts_code` 区间可用；
- `shibor` 必须传 `start_date`/`end_date`；`yc_cb` 无权限、`yield_curve` 接口名
  无效；DR007/MLF/OMO/净投放 无接口（降级为 Shibor 1W / LPR / M1·M2 方向）；
- `index_global` 无 VIX/SOX/美元指数代码（已实现波动率替代 VIX、USDCNH 替代美元）；
- `fx_daily` 的 `USDOLLAR.FXCM`/`USOil.FXCM`/`Copper.FXCM` 停更（2023-06-01），
  商品以国内 `fut_daily`（SC.INE/AU.SHF/CU.SHF）替代，标注“国内价、非 WTI/伦金”。
"""

from __future__ import annotations

import logging
import math
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# ===========================================================================
# 阈值与枚举常量（已确认决策：阈值首期集中在本文件顶部，回测后只改此处）
# ===========================================================================

# ---- 默认窗口（交易日口径） ----
DEFAULT_TECH_LOOKBACKS = (5, 20, 60, 120, 250)
DEFAULT_CALENDAR_WINDOWS = (5, 20, 60)
DEFAULT_GLOBAL_LOOKBACKS = (1, 5, 20)
SWING_LOOKBACK = 60  # 风格相对强弱（波段/长线）窗口

# ---- 指数与风格 ----
INDEX_UNIVERSE = (
    "000001.SH",  # 上证指数
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "000688.SH",  # 科创50
    "000016.SH",  # 上证50（大盘）
    "000852.SH",  # 中证1000（小盘）
    "000015.SH",  # 红利指数（价值）
)
INDEX_DISPLAY_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "000852.SH": "中证1000",
    "000015.SH": "红利指数",
}
# 风格相对强弱：值 > 0 表示前者强于后者（相对收益差，%）
STYLE_PAIRS = {
    "size": ("000016.SH", "000852.SH", "大盘vs小盘"),
    "growth_value": ("399006.SZ", "000015.SH", "成长vs价值"),
}
STYLE_ENUM = ("大盘成长", "大盘价值", "小盘成长", "小盘价值", "均衡", "信息不足")
SENTIMENT_CYCLE_ENUM = ("冰点", "修复", "高潮", "退潮", "信息不足")

# ---- 事件日历压力（第七章；金额单位：亿元，比例单位：小数） ----
IPO_AMOUNT_HIGH_YI = 150.0      # 窗口内 IPO 募资额合计 ≥ 该值 → 高
IPO_AMOUNT_MEDIUM_YI = 60.0     # 窗口内 IPO 募资额合计 ≥ 该值 → 中
UNLOCK_RATIO_MAX_HIGH = 0.20    # 单只解禁占流通比 ≥ 该值 → 高
UNLOCK_RATIO_MAX_MEDIUM = 0.10  # 单只解禁占流通比 ≥ 该值 → 中
UNLOCK_WINDOW_SUM_HIGH = 0.50   # 窗口内解禁占流通比合计 ≥ 该值 → 高
UNLOCK_WINDOW_SUM_MEDIUM = 0.20 # 窗口内解禁占流通比合计 ≥ 该值 → 中
UNLOCK_COUNT_HIGH = 50          # 窗口内解禁家数 ≥ 该值 → 高
UNLOCK_COUNT_MEDIUM = 20        # 窗口内解禁家数 ≥ 该值 → 中
EXPIRY_NEAR_TRADING_DAYS = 3    # 距交割 ≤ 该交易日数 → 中（3 分）
EXPIRY_MID_TRADING_DAYS = 10    # 距交割 ≤ 该交易日数 → 低（2 分）
MARGIN_CHANGE_RATE_HIGH = 0.02  # 融资余额变化率 ≤ -该值 → 高（去杠杆）
MARGIN_CHANGE_RATE_MEDIUM = 0.008  # ≤ -该值 → 中
PRESSURE_SCORE_HIGH = 4
PRESSURE_SCORE_MEDIUM = 3
PRESSURE_SCORE_LOW = 2
PRESSURE_SCORE_MIN = 1
CALENDAR_PRESSURE_ENUM = ("高", "中", "低", "信息不足")

# ---- 估值与流动性 ----
VALUATION_PCT_LOW = 30.0    # 分位 < 该值 → 低估
VALUATION_PCT_HIGH = 70.0   # 分位 > 该值 → 高估
VALUATION_MIN_SAMPLES = 250 # 分位计算最低样本数（对应 1 年交易日）
VALUATION_LEVEL_ENUM = ("低估", "合理", "高估", "信息不足")

# ---- 流动性方向（长线环境） ----
LIQUIDITY_RATE_CHANGE_BP = 5.0   # Shibor 1W 5 日变化（bp）达到该值 → 一个方向信号
LIQUIDITY_M2_CHANGE_PCT = 0.1    # M2 同比变化（百分点）达到该值 → 一个方向信号

# ---- 全球风险（第六章阶段 3 特征组） ----
GLOBAL_RISK_CORE_ITEMS = (
    "us_10y",       # 美债 10Y
    "term_spread",  # 期限利差（10Y-2Y）
    "equity_us",    # 美股指数
    "volatility",   # 波动率（已实现波动率替代 VIX）
    "fx_usdcnh",    # 离岸人民币
    "commodity_cn", # 国内商品（SC/AU/CU）
)
GLOBAL_RISK_CORE_MIN_RATIO = 0.5  # 核心价格可用比例低于该值 → insufficient
REALIZED_VOL_ANNUALIZE = math.sqrt(252.0)
REALIZED_VOL_MIN_SAMPLES = 21     # 20 日已实现波动率所需样本（含首值）

# ---- 全局风险级别/门控枚举（第十二章） ----
SYSTEMIC_RISK_ENUM = ("high", "medium", "low", "insufficient")
CONFIDENCE_ENUM = ("high", "medium", "low")
CONFIDENCE_NOT_BELOW_MEDIUM = ("high", "medium")
SHORT_TERM_ENUM = ("适合", "谨慎", "回避", "信息不足")
WAVE_ENUM = ("进攻", "平衡", "防御", "信息不足")
LONG_TERM_ENUM = ("配置窗口", "等待窗口", "信息不足")
RISK_GATE_ENUM = ("normal", "caution", "block")
RISK_GATE_FALLBACK = "caution"  # 数据不足/解析失败默认（已确认决策：fail-open → caution）
# 全球风险评估（第六章）：风险偏好方向 + 证据角色标识
RISK_APPETITE_ENUM = ("进攻", "中性", "避险", "信息不足")
GLOBAL_RISK_EVIDENCE_ROLE_ENUM = (
    "risk_price", "event_fact", "history_reference", "credit_liquidity", "other",
)

# ---- 数据质量 ----
DEGRADATION_LEVEL_ENUM = ("ok", "partial", "insufficient")
DATASET_STATUS_ENUM = ("ok", "partial", "missing", "insufficient")
CORE_MISSING_STATUSES = ("missing", "insufficient")

# ---- 8 个结构化接口 ↔ 数据集清单（第十二章结构化接口例外组） ----
MARKET_DATASETS = {
    "market_index_features": {
        "interface": "get_market_index_features",
        "core": False,
        "desc": "7 指数 OHLCV 完整窗口（宽基趋势/风格）",
    },
    "market_breadth_history": {
        "interface": "get_market_breadth_history",
        "core": True,
        "desc": "宽度与高标情绪序列（短线判据组）",
    },
    "market_fund_flow_history": {
        "interface": "get_market_fund_flow_history",
        "core": True,
        "desc": "主力/北向资金序列（短线判据组：成交额/资金）",
    },
    "margin_trading_history": {
        "interface": "get_margin_trading_history",
        "core": False,
        "desc": "两融余额历史序列（资金 1/5/20 日变化）",
    },
    "market_valuation": {
        "interface": "get_market_valuation",
        "core": False,
        "desc": "指数 PE/PB 历史分位与全 A 快照（长线判据组）",
    },
    "cn_liquidity_indicators": {
        "interface": "get_cn_liquidity_indicators",
        "core": False,
        "desc": "Shibor/LPR/M1·M2（长线判据组；10Y/DR007 降级）",
    },
    "cn_event_calendar": {
        "interface": "get_cn_event_calendar",
        "core": False,
        "desc": "IPO/解禁/交割/长假（CN News 资金日历）",
    },
    "global_risk_indicators": {
        "interface": "get_global_risk_indicators",
        "core": True,
        "desc": "全球风险价格（阶段 3 特征组，`global_risk_assessment` 证据）",
    },
}
# 核心输入清单：`global_risk_assessment` 风险价格证据 + `market_regime` 短线判据组
CORE_DATASET_NAMES = tuple(
    name for name, meta in MARKET_DATASETS.items() if meta.get("core")
)

# ---- 级别最低证据（第八章；至少两类，否则“信息不足”） ----
MIN_EVIDENCE_GROUPS = 2
LEVEL_EVIDENCE_GROUPS = {
    "short_term": ("breadth", "turnover", "sentiment"),
    "wave": ("index_ma", "fund_trend", "style"),
    "long_term": ("index_ma_long", "valuation", "liquidity"),
}
PARSE_STATUS_OK = "ok"
PARSE_STATUS_DEGRADED = "degraded"

# ===========================================================================
# 基础工具
# ===========================================================================

_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d")


def normalize_date(value: Any) -> Optional[str]:
    """把多种日期写法归一为 `YYYY-MM-DD`；无法解析返回 `None`。"""
    if value is None:
        return None
    if isinstance(value, (_datetime, _date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return _datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> Optional[float]:
    """安全转 float；空值/非数值返回 `None`（字段变化降级而非异常）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in ("nan", "none", "n/a", "-", "--", ""):
        return None
    try:
        val = float(text)
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def _clean_series(values: Optional[Iterable[Any]]) -> list:
    """保留 None 占位（长度对齐），仅把非数值转为 `None`。"""
    if not values:
        return []
    return [_to_float(v) for v in values]


def _compact(values: Iterable[Optional[float]]) -> list:
    """去掉 `None` 后的有效数值列表。"""
    return [v for v in values if v is not None]


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _pct_rank(history: list, value: Optional[float]) -> Optional[float]:
    """`value` 在 `history` 中的百分位（0-100，含等值折算为半数）。"""
    if value is None or not history:
        return None
    below = sum(1 for h in history if h < value)
    equal = sum(1 for h in history if h == value)
    return round((below + equal / 2.0) / len(history) * 100.0, 2)


def _ols_slope_pct(values: list) -> Optional[float]:
    """归一化 OLS 斜率：斜率 / 均价 × 100（单位：%/交易日）。"""
    n = len(values)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    if mean_y == 0:
        return None
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return None
    numer = sum((i - mean_x) * (values[i] - mean_y) for i in range(n))
    return round(numer / denom / mean_y * 100.0, 4)


def _realized_vol_pct(values: list) -> Optional[float]:
    """已实现波动率（年化，%）：对数收益标准差 × sqrt(252)。"""
    vals = [v for v in values if v is not None and v > 0]
    if len(vals) < 2:
        return None
    rets = [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals))]
    if len(rets) < 2:
        return None
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * REALIZED_VOL_ANNUALIZE * 100.0, 2)


def _window_metric(values: list, window: int, fn: Callable[[list], Optional[float]],
                   unit: str = "") -> dict:
    """对序列尾部 `window` 个有效样本应用 `fn`；样本不足返回 value=None。

    Returns:
        {"value", "window", "samples", "sufficient", "unit"}
    """
    vals = _compact(values)
    if len(vals) < window or window <= 0:
        return {
            "value": None,
            "window": window,
            "samples": len(vals),
            "sufficient": False,
            "unit": unit,
        }
    tail = vals[-window:]
    value = fn(tail)
    return {
        "value": _round(value),
        "window": window,
        "samples": window,
        "sufficient": value is not None,
        "unit": unit,
    }


def _return_of(tail: list) -> Optional[float]:
    """窗口收益率（%）：`close[-1]/close[0]-1`，要求首值非 0。"""
    if not tail or tail[0] in (None, 0):
        return None
    if tail[-1] is None:
        return None
    return (tail[-1] / tail[0] - 1.0) * 100.0


def _level_from_score(score: Optional[int]) -> str:
    """压力分数 → 压力等级（高/中/低/信息不足）。"""
    if score is None:
        return "信息不足"
    if score >= PRESSURE_SCORE_HIGH:
        return "高"
    if score >= PRESSURE_SCORE_MEDIUM:
        return "中"
    return "低"


def _metric_value(metrics: dict, window: int, key: str = "value") -> Optional[float]:
    """安全读取 {window: metric} 结构中的值。"""
    entry = (metrics or {}).get(window)
    if not isinstance(entry, dict):
        return None
    return entry.get(key)


# ===========================================================================
# 序列截断：未来数据丢弃 + as_of
# ===========================================================================

def _split_future(rows: list, as_of: Optional[str], date_key: str = "trade_date") -> tuple:
    """按 `date_key` 丢弃晚于 `as_of` 的行。

    Returns:
        (保留的行, 被丢弃条数, 无法解析日期的条数)
    """
    kept, dropped, invalid = [], 0, 0
    for row in rows or []:
        if not isinstance(row, dict):
            invalid += 1
            continue
        raw_date = row.get(date_key)
        norm = normalize_date(raw_date)
        if norm is None:
            invalid += 1
            continue
        if as_of and norm > as_of:
            dropped += 1
            continue
        new_row = dict(row)
        new_row[date_key] = norm
        kept.append(new_row)
    kept.sort(key=lambda r: r[date_key])
    return kept, dropped, invalid


def _numeric_series(rows: list, key: str, date_key: str = "trade_date") -> tuple:
    """从行列表提取 (dates, values)；缺失值保留 None 以对齐长度。"""
    dates = [r.get(date_key) for r in rows or []]
    values = [_to_float((r or {}).get(key)) for r in rows or []]
    return dates, values


_AS_OF_UNPARSABLE_NOTE = "as_of 不可解析，防前视未生效"


def _finalize_features(result: dict) -> dict:
    """特征组返回前收尾（评审残留）：`as_of` 不可解析时补 `future_data_undeterminable`。

    各特征组按 `curr_date` 调 `_split_future` 丢弃未来行；`curr` 不可解析时
    截断条件（`norm > as_of`）恒不成立，**防前视静默失效**。notes 已显式标注，
    此处再置标志位——「无法判定」不得被下游当成「无未来数据」。
    """
    notes = result.get("notes") or []
    if any(_AS_OF_UNPARSABLE_NOTE in str(note) for note in notes):
        result["future_data_undeterminable"] = True
        data_quality = result.get("data_quality")
        if isinstance(data_quality, dict):
            data_quality["future_data_undeterminable"] = True
    return result


def _quality_block(status: str, actual_dates: dict, missing_inputs: list,
                   future_data_detected: bool, notes: Optional[list] = None) -> dict:
    """节点/特征级 `data_quality` 子字段（第十二章判据 (b)/(d) 消费）。"""
    if status not in DEGRADATION_LEVEL_ENUM:
        status = "insufficient"
    return {
        "degradation_level": status,
        "actual_dates": {k: v for k, v in (actual_dates or {}).items() if v},
        "missing_inputs": list(missing_inputs or []),
        "future_data_detected": bool(future_data_detected),
        "notes": list(notes or []),
    }


# ===========================================================================
# 数据质量汇总（图启动单点写入）
# ===========================================================================

def build_data_quality_summary(analysis_date: str, datasets: Optional[dict] = None) -> dict:
    """构建 `market_data_quality` 启动快照（图启动阶段一次性写入 State）。

    Args:
        analysis_date: 分析日期（交易日，`YYYY-MM-DD`）。
        datasets: `{数据集名: {"status", "actual_date", "measured", "core", "note"}}`；
            通常由 :func:`probe_dataset_availability` 生成（零 API 调用的能力快照），
            也可由调用方注入实测结果（此时 `measured=True`）。

    Returns:
        dict，包含 `analysis_date`、`datasets`、`missing_inputs`、
        `core_missing_inputs`、`core_insufficient`、`degradation_level`、
        `future_data_detected`。节点不得回写该键（消费见 `derive_risk_gate` 判据 (a)）。
    """
    normalized: dict = {}
    for name, meta in MARKET_DATASETS.items():
        raw = (datasets or {}).get(name) or {}
        if not isinstance(raw, dict):
            raw = {"status": "unknown", "note": "数据集元信息格式异常"}
        status = raw.get("status") or "missing"
        if status not in DATASET_STATUS_ENUM:
            status = "missing"
        actual_date = normalize_date(raw.get("actual_date"))
        measured = bool(raw.get("measured"))
        core = bool(raw.get("core", meta.get("core", False)))
        note = raw.get("note") or meta.get("desc", "")
        normalized[name] = {
            "status": status,
            "actual_date": actual_date,
            "measured": measured,
            "core": core,
            "interface": meta.get("interface", ""),
            "note": note,
        }

    # 判据 (a)：核心输入 status ∈ {missing, insufficient}，或已实测条目缺实际日期
    core_bad, noncore_bad, partial = [], [], []
    for name, entry in normalized.items():
        bad_status = entry["status"] in CORE_MISSING_STATUSES
        missing_date = entry["measured"] and not entry["actual_date"]
        if entry["core"] and (bad_status or missing_date):
            core_bad.append(name)
        elif bad_status:
            noncore_bad.append(name)
        elif entry["status"] == "partial":
            partial.append(name)

    if core_bad:
        level = "insufficient"
    elif noncore_bad or partial:
        level = "partial"
    else:
        level = "ok"

    missing_inputs = sorted(set(core_bad) | set(noncore_bad) | set(partial))
    notes = []
    if core_bad:
        notes.append("核心输入缺失/不足，风险门控上限为 caution（第十二章规则 3）")
    if not datasets:
        notes.append("未提供数据源能力快照，按全部缺失处理（fail-closed → caution）")

    return {
        "analysis_date": normalize_date(analysis_date) or str(analysis_date or ""),
        "datasets": normalized,
        "missing_inputs": missing_inputs,
        "core_missing_inputs": sorted(core_bad),
        "core_insufficient": bool(core_bad),
        "degradation_level": level,
        "future_data_detected": False,
        "notes": notes,
    }


def probe_dataset_availability(analysis_date: Optional[str] = None) -> dict:
    """零 API 调用的数据源能力快照（覆写检测，不实例化 Provider）。

    通过 `interface.market_dataset_support()` 判断当前数据源是否覆写了 8 个结构化
    接口。`measured=False`：能力存在不代表当日有数据，运行中途暴露的缺失由各节点
    `data_quality` 子字段描述（第十二章判据 (b)）。

    Returns:
        `{数据集名: {"status", "actual_date", "measured", "core", "interface", "note"}}`
    """
    support = {}
    try:
        from AI.dataflows import interface as dataflow

        support = dataflow.market_dataset_support() or {}
    except Exception as exc:  # 探测失败按缺失处理，不阻断
        logger.warning("[market_features] 数据源能力探测失败: %s", exc)

    datasets: dict = {}
    for name, meta in MARKET_DATASETS.items():
        supported = bool(support.get(name))
        datasets[name] = {
            "status": "ok" if supported else "missing",
            "actual_date": None,
            "measured": False,
            "core": bool(meta.get("core")),
            "interface": meta.get("interface"),
            "note": (
                "能力探测（接口覆写检测）：{}".format(meta.get("desc", ""))
                if supported
                else "能力探测：当前数据源未覆写 {}".format(meta.get("interface"))
            ),
        }
    if analysis_date:
        logger.debug("[market_features] 能力快照 @ %s: %s", analysis_date, datasets)
    return datasets


# ===========================================================================
# 节点级数据质量子字段（第十二章判据 (b)/(d)）
# ===========================================================================

def node_data_quality(features: dict, parsed: dict,
                      insufficient_keys: Iterable[str] = ("short_term",),
                      extra: Optional[dict] = None) -> dict:
    """消费节点输出的 `data_quality` 子字段：特征层质量 + 输出级不足枚举。

    判据 (d)：节点输出为不足枚举（`systemic_risk=insufficient` 或短线=信息不足）
    时，其 `data_quality` 子字段必须标注 `insufficient`，否则视为违约写实现错误；
    结构解析失败（判据 (c)）同样降级为 `insufficient`。

    Args:
        features: 特征层返回值（取其中 `data_quality` 与 `as_of_date`）。
        parsed: 该节点的结构化输出（`parse_status` / 各级别 `level`）。
        insufficient_keys: 触发不足的级别键（默认短线；日历节点同口径）。
        extra: 需要合并进子字段的额外键。

    Returns:
        dict（`degradation_level` / `actual_dates` / `missing_inputs` /
        `future_data_detected` / `as_of_date` / `parse_status` / `notes`）。
    """
    features = features if isinstance(features, dict) else {}
    parsed = parsed if isinstance(parsed, dict) else {}
    quality = features.get("data_quality")
    dq = dict(quality) if isinstance(quality, dict) else {}
    dq["degradation_level"] = dq.get("degradation_level") or "insufficient"
    dq["actual_dates"] = dict(dq.get("actual_dates") or {})
    dq["missing_inputs"] = list(dq.get("missing_inputs") or [])
    dq["future_data_detected"] = bool(dq.get("future_data_detected"))
    dq["notes"] = list(dq.get("notes") or [])
    if not dq["actual_dates"] and features.get("as_of_date"):
        dq["actual_dates"] = {"features_as_of_date": features.get("as_of_date")}

    dq["as_of_date"] = features.get("as_of_date")
    dq["parse_status"] = parsed.get("parse_status")

    if parsed.get("parse_status") not in (None, PARSE_STATUS_OK):
        dq["degradation_level"] = "insufficient"
        note = parsed.get("parse_note")
        if note:
            dq["notes"].append(str(note))

    unmet = [key for key in (insufficient_keys or ())
             if isinstance(parsed.get(key), dict)
             and parsed[key].get("level") == "信息不足"]
    if unmet:
        dq["degradation_level"] = "insufficient"
        dq["insufficient_levels"] = unmet
    if extra:
        dq.update(extra)
    return dq


# ===========================================================================
# 交易日口径工具
# ===========================================================================

def _trading_days_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
    """`start < d <= end` 的交易日数量；日历不可用时返回 `None`（降级）。"""
    if not start or not end or start >= end:
        return 0 if start == end else None
    try:
        from AI.dataflows.utils.trading_calendar import is_trading_day

        cursor = _datetime.strptime(start, "%Y-%m-%d").date()
        end_date = _datetime.strptime(end, "%Y-%m-%d").date()
        if (end_date - cursor).days > 400:
            return None
        count = 0
        while cursor < end_date:
            cursor += _timedelta(days=1)
            if is_trading_day(cursor.strftime("%Y-%m-%d")):
                count += 1
        return count
    except Exception as exc:
        logger.warning("[market_features] 交易日计数失败: %s", exc)
        return None


def _nth_trading_day_after(start: Optional[str], n: int) -> Optional[str]:
    """`start` 之后第 `n` 个交易日（n>=1）；日历不可用时返回 `None`。"""
    if not start or n <= 0:
        return None
    try:
        from AI.dataflows.utils.trading_calendar import is_trading_day

        cursor = _datetime.strptime(start, "%Y-%m-%d").date()
        remaining = n
        for _ in range(n * 3 + 30):  # 覆盖长假
            cursor += _timedelta(days=1)
            if is_trading_day(cursor.strftime("%Y-%m-%d")):
                remaining -= 1
                if remaining == 0:
                    return cursor.strftime("%Y-%m-%d")
        return None
    except Exception as exc:
        logger.warning("[market_features] 交易日偏移失败: %s", exc)
        return None


def _safe_call(fn_name: str, *args, **kwargs) -> Any:
    """调用 interface 层结构化接口；异常/缺失一律返回 `None`（降级不阻断）。

    Returns:
        Provider 返回的 dict，或 `None`。
    """
    try:
        from AI.dataflows import interface as dataflow

        fn = getattr(dataflow, fn_name, None)
        if fn is None:
            logger.warning("[market_features] interface 缺少接口 %s", fn_name)
            return None
        result = fn(*args, **kwargs)
        if result is None:
            logger.info("[market_features] %s 返回 None（数据不可用，降级）", fn_name)
        return result
    except Exception as exc:
        logger.warning("[market_features] %s 调用异常，降级: %s", fn_name, exc)
        return None


def _series_metrics(rows: list, key: str, windows: Iterable[int] = (1, 5, 20),
                    unit: str = "") -> dict:
    """单序列 → `{"latest", "as_of_date", "samples", "ma": {}, "change": {}, "sum": {}}`。

    窗口 `N` 口径：最近 N 个有效样本（`N-1` 条即不足，指标为 `None`）。
    """
    dates, values = _numeric_series(rows, key)
    pairs = [(d, v) for d, v in zip(dates, values) if v is not None]
    clean_dates = [d for d, _ in pairs]
    clean_values = [v for _, v in pairs]
    windows = tuple(sorted({int(w) for w in windows if int(w) > 0})) or (1,)

    metric = {
        "latest": _round(clean_values[-1]) if clean_values else None,
        "as_of_date": clean_dates[-1] if clean_dates else None,
        "samples": len(clean_values),
        "ma": {},
        "change": {},
        "sum": {},
        "unit": unit,
    }
    for w in windows:
        metric["ma"][w] = _window_metric(clean_values, w, _mean, unit)
        metric["change"][w] = _window_metric(
            clean_values, w,
            lambda tail: (tail[-1] - tail[0]) if len(tail) >= 2 else None,
            unit,
        )
        metric["change"][w]["pct"] = _window_metric(clean_values, w, _return_of, "%")
        metric["sum"][w] = _window_metric(clean_values, w, sum, unit)
    return metric


# ===========================================================================
# 特征 1：CN Tech（第四、八章）
# ===========================================================================

def build_cn_technical_features(curr_date: str,
                                lookbacks: Iterable[int] = DEFAULT_TECH_LOOKBACKS) -> dict:
    """构建 CN Tech 市场技术特征（指数/宽度/资金/估值/流动性）。

    Args:
        curr_date: 分析日（交易日）。
        lookbacks: 趋势窗口（交易日口径），默认 `(5, 20, 60, 120, 250)`。

    Returns:
        dict：`as_of_date` / `analysis_date` / `lookbacks` / `raw_reference` /
        `derived_metrics` / `data_quality` / `future_data_detected` /
        `missing_inputs` / `notes`。样本不足的指标 `value=None`、`sufficient=False`。
    """
    curr = normalize_date(curr_date)
    lbs = tuple(sorted({int(w) for w in (lookbacks or DEFAULT_TECH_LOOKBACKS) if int(w) > 0}))
    lbs = lbs or DEFAULT_TECH_LOOKBACKS
    max_lb = max(lbs)

    missing_inputs: list = []
    notes: list = []
    if curr is None and str(curr_date or "").strip():
        # as_of 不可解析 → `_split_future` 截断恒不生效，防前视**静默失效**：
        # 显式标注（评审残留），不得用 str 兜底造出垃圾 as_of
        notes.append(_AS_OF_UNPARSABLE_NOTE)
    future_detected = False
    actual_dates: dict = {}
    raw_reference: dict = {}
    derived: dict = {}
    as_of_candidates: list = []

    # ---- 指数（趋势/风格） ----
    index_payload = _safe_call("get_market_index_features", curr, lookbacks=lbs)
    raw_indices: dict = {}
    indices_metrics: dict = {}
    turnover_series: dict = {}
    if isinstance(index_payload, dict) and index_payload.get("indices"):
        payload_as_of = normalize_date(index_payload.get("as_of_date"))
        raw_missing = index_payload.get("missing") or {}
        for code, series in (index_payload.get("indices") or {}).items():
            if not isinstance(series, dict):
                continue
            raw_dates = series.get("trade_dates") or []
            closings = _clean_series(series.get("close"))
            amounts = _clean_series(series.get("amount"))
            # 按位置对齐（而非 zip）：缺 amount/close 时不得整体截断为 0 行
            rows = [
                {"trade_date": d,
                 "close": closings[i] if i < len(closings) else None,
                 "amount": amounts[i] if i < len(amounts) else None}
                for i, d in enumerate(raw_dates)
            ]
            rows, dropped, _ = _split_future(rows, curr)
            if dropped:
                future_detected = True
                notes.append(f"{code} 序列含 {dropped} 条晚于 {curr} 的数据，已丢弃")
            dates = [r["trade_date"] for r in rows]
            close_values = [r.get("close") for r in rows]
            amount_values = [r.get("amount") for r in rows]
            pairs = [(d, v) for d, v in zip(dates, close_values) if v is not None]
            if not pairs:
                continue
            dates = [d for d, _ in pairs]
            clean_close = [v for _, v in pairs]
            amt_pairs = [(d, v) for d, v in zip(dates, amount_values) if v is not None]
            raw_reference.setdefault("indices", {})[code] = {
                "trade_dates": dates[-min(len(dates), max_lb + 5):],
                "close": clean_close[-min(len(dates), max_lb + 5):],
            }
            if amt_pairs:
                turnover_series[code] = dict(amt_pairs)

            entry = {
                "name": INDEX_DISPLAY_NAMES.get(code, code),
                "as_of_date": dates[-1] if dates else None,
                "samples": len(clean_close),
                "close": {
                    "value": _round(clean_close[-1]),
                    "window": 1,
                    "samples": len(clean_close),
                    "sufficient": True,
                    "unit": "点",
                },
                "ma": {w: _window_metric(clean_close, w, _mean, "点") for w in lbs},
                "return": {w: _window_metric(clean_close, w, _return_of, "%") for w in lbs},
                "slope": {w: _window_metric(clean_close, w, _ols_slope_pct, "%/日")
                          for w in lbs},
            }
            amount_values_clean = [v for _, v in amt_pairs]
            if amount_values_clean:
                entry["amount_ma"] = {
                    w: _window_metric(amount_values_clean, w, _mean, "千元") for w in lbs
                }
            indices_metrics[code] = entry
            if dates:
                as_of_candidates.append(dates[-1])
        if raw_missing:
            missing_inputs.extend(f"index:{k}" for k in raw_missing)
            notes.append("指数缺失：{}".format(
                "、".join(f"{k}({v})" for k, v in raw_missing.items())))
        if not indices_metrics:
            missing_inputs.append("market_index_features")
            notes.append("指数特征无可用样本（窗口内为空或全部晚于分析日）")
        if payload_as_of:
            actual_dates["market_index_features"] = payload_as_of
    else:
        missing_inputs.append("market_index_features")
        notes.append("指数特征不可用：宽基趋势/风格证据缺失")

    # 风格相对强弱（窗口 60）
    style_metrics: dict = {}
    for style_key, (big, small, label) in STYLE_PAIRS.items():
        big_metric = (indices_metrics.get(big) or {}).get("return", {}).get(SWING_LOOKBACK)
        small_metric = (indices_metrics.get(small) or {}).get("return", {}).get(SWING_LOOKBACK)
        big_val = (big_metric or {}).get("value")
        small_val = (small_metric or {}).get("value")
        if big_val is None or small_val is None:
            style_metrics[style_key] = {
                "label": label,
                "value": None,
                "window": SWING_LOOKBACK,
                "samples": min((big_metric or {}).get("samples", 0),
                               (small_metric or {}).get("samples", 0)),
                "sufficient": False,
                "note": "对应指数窗口样本不足或缺失",
            }
            continue
        style_metrics[style_key] = {
            "label": label,
            "value": _round(big_val - small_val),
            "window": SWING_LOOKBACK,
            "samples": SWING_LOOKBACK,
            "sufficient": True,
            "unit": "pp（相对收益差）",
        }
    style_label = _derive_style_label(style_metrics)
    derived["style"] = {"pairs": style_metrics, "label": style_label}

    # ---- 宽度与高标情绪 ----
    breadth_payload = _safe_call("get_market_breadth_history", curr, days=max(20, 5))
    breadth = {"as_of_date": None, "samples": 0, "series": {}, "missing": []}
    breadth_rows: list = []
    if isinstance(breadth_payload, dict) and breadth_payload.get("series"):
        rows, dropped, _ = _split_future(breadth_payload.get("series") or [], curr)
        breadth_rows = rows
        if dropped:
            future_detected = True
            notes.append(f"宽度/情绪序列含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        if rows:
            breadth = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "missing": list((breadth_payload.get("missing") or {}).keys()),
                "series": {
                    key: _series_metrics(rows, key, (5, 20), "")
                    for key in ("up_ratio", "limit_up", "limit_down", "broken_board_rate",
                                "max_board", "promotion_rate", "premium_rate")
                },
            }
            as_of_candidates.append(breadth["as_of_date"])
            actual_dates["market_breadth_history"] = breadth["as_of_date"]
        if breadth_payload.get("missing"):
            notes.append("高标情绪部分缺失：{}".format(
                "；".join(f"{k}({v})" for k, v in (breadth_payload.get("missing") or {}).items())))
    else:
        missing_inputs.append("market_breadth_history")
        notes.append("宽度/高标情绪序列不可用")
    derived["breadth"] = breadth

    # ---- 成交额（上证 + 深证成指 amount 合计，千元）----
    # 指数 amount 缺失时回退到宽度序列的全市场成交额合计（同一单位：千元）。
    turnover_rows: list = []
    turnover_source = None
    if turnover_series:
        common_dates = None
        for _, series in turnover_series.items():
            keys = set(series.keys())
            common_dates = keys if common_dates is None else (common_dates & keys)
        common_dates = sorted(common_dates or [])
        # 上证与深证同时可用时用合计，否则退化为可用项
        usable = [c for c in ("000001.SH", "399001.SZ") if c in turnover_series]
        if common_dates and usable:
            turnover_rows = [
                {"trade_date": d, "amount": sum(turnover_series[c][d] for c in usable)}
                for d in common_dates
            ]
            turnover_source = "index_amount:" + "+".join(usable)
    if not turnover_rows and breadth_rows:
        fallback_rows = [
            {"trade_date": r.get("trade_date"), "amount": _to_float(r.get("market_amount"))}
            for r in breadth_rows
        ]
        fallback_rows = [r for r in fallback_rows
                         if r["trade_date"] and r["amount"] is not None]
        if fallback_rows:
            turnover_rows = fallback_rows
            turnover_source = "breadth_market_amount"
            notes.append("成交额回退：指数 amount 不可用，改用宽度序列的全市场成交额合计（千元）")
    turnover: dict = {}
    if turnover_rows:
        turnover = {
            "as_of_date": turnover_rows[-1]["trade_date"],
            "samples": len(turnover_rows),
            "source": turnover_source,
            "series": _series_metrics(turnover_rows, "amount", (5, 20), "千元"),
        }
        turnover["change_5d_pct"] = _window_metric(
            [r["amount"] for r in turnover_rows], 5, _return_of, "%")
        if turnover["as_of_date"]:
            as_of_candidates.append(turnover["as_of_date"])
    else:
        missing_inputs.append("turnover")
        notes.append("成交额不可用（指数 amount 与宽度成交额均缺失），短线“成交额”判据缺失")
    derived["turnover"] = turnover

    # ---- 资金（主力/北向/两融） ----
    fund_payload = _safe_call("get_market_fund_flow_history", curr, days=max(20, 5))
    fund_flow = {"as_of_date": None, "samples": 0, "main_net": {}, "northbound": {}}
    if isinstance(fund_payload, dict) and fund_payload.get("series"):
        rows, dropped, _ = _split_future(fund_payload.get("series") or [], curr)
        if dropped:
            future_detected = True
            notes.append(f"资金序列含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        if rows:
            fund_flow = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "main_net": _series_metrics(rows, "main_net_amount", (5, 20), "万元",
                                            ),
                "northbound": _series_metrics(rows, "northbound_net", (5, 20), "万元"),
            }
            as_of_candidates.append(fund_flow["as_of_date"])
            actual_dates["market_fund_flow_history"] = fund_flow["as_of_date"]
        if fund_payload.get("missing"):
            notes.append("资金序列部分缺失：{}".format(
                "；".join(f"{k}({v})" for k, v in (fund_payload.get("missing") or {}).items())))
    else:
        missing_inputs.append("market_fund_flow_history")
        notes.append("资金序列不可用（主力/北向趋势缺失）")
    derived["fund_flow"] = fund_flow

    margin_payload = _safe_call("get_margin_trading_history", curr, days=max(20, 5))
    margin = {"as_of_date": None, "samples": 0}
    if isinstance(margin_payload, dict) and margin_payload.get("series"):
        rows, dropped, _ = _split_future(margin_payload.get("series") or [], curr)
        if dropped:
            future_detected = True
            notes.append(f"两融序列含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        if rows:
            rzye = _series_metrics(rows, "rzye", (1, 5, 20), "元")
            rqye = _series_metrics(rows, "rqye", (5, 20), "元")
            margin = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "rzye": rzye,
                "rqye": rqye,
            }
            as_of_candidates.append(margin["as_of_date"])
            actual_dates["margin_trading_history"] = margin["as_of_date"]
    else:
        missing_inputs.append("margin_trading_history")
        notes.append("两融序列不可用（融资余额 1/5/20 日变化缺失）")
    derived["margin"] = margin

    # ---- 估值（长线） ----
    valuation = _build_valuation_metrics(_safe_call("get_market_valuation", curr), curr)
    if valuation.get("level") == "信息不足":
        missing_inputs.append("market_valuation")
    if valuation.get("as_of_date"):
        as_of_candidates.append(valuation["as_of_date"])
        actual_dates["market_valuation"] = valuation["as_of_date"]
    if valuation.get("future_data_detected"):
        future_detected = True
        notes.extend(valuation.get("drops") or [])

    # ---- 流动性（长线） ----
    liquidity = _build_liquidity_metrics(
        _safe_call("get_cn_liquidity_indicators", curr, days=20), curr)
    if liquidity.get("direction") == "信息不足":
        missing_inputs.append("cn_liquidity_indicators")
    if liquidity.get("as_of_date"):
        as_of_candidates.append(liquidity["as_of_date"])
        actual_dates["cn_liquidity_indicators"] = liquidity["as_of_date"]
    if liquidity.get("future_data_detected"):
        future_detected = True
        notes.extend(liquidity.get("drops") or [])

    derived["indices"] = indices_metrics
    derived["valuation"] = valuation
    derived["liquidity"] = liquidity

    # ---- 级别最低证据覆盖（第八章：至少两类，否则“信息不足”） ----
    coverage = _level_evidence_coverage(derived, lbs)
    derived["evidence_coverage"] = coverage

    as_of = min(as_of_candidates) if as_of_candidates else None
    if not as_of:
        notes.append("所有输入均不可用：特征全部为信息不足（不阻断全图）")
    status = "ok"
    if missing_inputs and len(missing_inputs) >= 3:
        status = "insufficient"
    elif missing_inputs:
        status = "partial"

    return _finalize_features({
        "as_of_date": as_of,
        "analysis_date": curr,
        "lookbacks": list(lbs),
        "raw_reference": raw_reference,
        "derived_metrics": derived,
        "data_quality": _quality_block(
            status, actual_dates, missing_inputs, future_detected, notes),
        "future_data_detected": bool(future_detected),
        "missing_inputs": sorted(set(missing_inputs)),
        "notes": notes,
    })


def _derive_style_label(style_metrics: dict) -> str:
    """由大小盘/成长价值相对强弱派生风格枚举（缺失或不足 → 信息不足）。"""
    size = (style_metrics or {}).get("size") or {}
    growth = (style_metrics or {}).get("growth_value") or {}
    if not size.get("sufficient") or not growth.get("sufficient"):
        return "信息不足"
    size_val, growth_val = size.get("value") or 0.0, growth.get("value") or 0.0
    size_label = "大盘" if size_val > 0 else "小盘"
    growth_label = "成长" if growth_val > 0 else "价值"
    if abs(size_val) < 1.0 and abs(growth_val) < 1.0:
        return "均衡"
    return f"{size_label}{growth_label}"


def _level_evidence_coverage(derived: dict, lookbacks: tuple) -> dict:
    """判定三级别各自的最低证据组可用性（第八章：至少两类）。"""
    indices = derived.get("indices") or {}
    breadth = derived.get("breadth") or {}
    breadth_series = breadth.get("series") or {}
    turnover = derived.get("turnover") or {}
    fund_flow = derived.get("fund_flow") or {}
    margin = derived.get("margin") or {}
    style = (derived.get("style") or {}).get("pairs") or {}
    valuation = derived.get("valuation") or {}
    liquidity = derived.get("liquidity") or {}

    def _series_ok(entry: dict, window: int, key: str = "latest") -> bool:
        if key == "latest":
            return entry.get("samples", 0) >= window and entry.get("latest") is not None
        metric = (entry.get(key) or {}).get(window) or {}
        return bool(metric.get("sufficient"))

    index_ma_ok = any(
        _metric_value((indices.get(code) or {}).get("ma") or {}, 20) is not None
        and _metric_value((indices.get(code) or {}).get("ma") or {}, 60) is not None
        for code in ("000001.SH", "399001.SZ", "399006.SZ")
    )
    index_ma_long_ok = any(
        _metric_value((indices.get(code) or {}).get("ma") or {}, 120) is not None
        and _metric_value((indices.get(code) or {}).get("ma") or {}, 250) is not None
        for code in ("000001.SH", "399001.SZ", "399006.SZ")
    )
    breadth_ok = (_series_ok(breadth_series.get("up_ratio") or {}, 5)
                  or _series_ok(breadth_series.get("limit_up") or {}, 5))
    turnover_ma = (turnover.get("series") or {}).get("ma") or {}
    turnover_ok = _metric_value(turnover_ma, 5) is not None
    sentiment_ok = (_series_ok(breadth_series.get("promotion_rate") or {}, 5)
                    or _series_ok(breadth_series.get("broken_board_rate") or {}, 5)
                    or _series_ok(breadth_series.get("max_board") or {}, 5))
    fund_trend_ok = (_metric_value(margin.get("rzye", {}).get("change") or {}, 5) is not None
                     or _metric_value(fund_flow.get("main_net", {}).get("sum") or {}, 5) is not None)
    style_ok = any((entry or {}).get("sufficient") for entry in style.values())
    valuation_ok = valuation.get("level") not in (None, "信息不足")
    liquidity_ok = liquidity.get("direction") not in (None, "信息不足")

    groups = {
        "breadth": breadth_ok,
        "turnover": turnover_ok,
        "sentiment": sentiment_ok,
        "index_ma": index_ma_ok,
        "fund_trend": fund_trend_ok,
        "style": style_ok,
        "index_ma_long": index_ma_long_ok,
        "valuation": valuation_ok,
        "liquidity": liquidity_ok,
    }
    coverage = {}
    for level, keys in LEVEL_EVIDENCE_GROUPS.items():
        available = [k for k in keys if groups.get(k)]
        missing = [k for k in keys if not groups.get(k)]
        coverage[level] = {
            "groups": list(keys),
            "available": available,
            "missing": missing,
            "available_count": len(available),
            "satisfied": len(available) >= MIN_EVIDENCE_GROUPS,
            "note": "" if len(available) >= MIN_EVIDENCE_GROUPS else "不足两类 → 该级别应输出“信息不足”",
        }
    return coverage


def _build_valuation_metrics(payload: Optional[dict], curr: str) -> dict:
    """估值特征：指数 PE/PB 历史分位 + 全 A 快照（缺失 → 信息不足）。

    `curr` 为分析日上界：晚于该日的行先经 `_split_future` 丢弃（评审 m13，
    与其余特征组同口径），再算分位与最新值——防未来数据污染分位。
    """
    result = {
        "as_of_date": None,
        "level": "信息不足",
        "index": {},
        "all_a": {},
        "primary": None,
        "missing": [],
        "drops": [],
        "future_data_detected": False,
    }
    if not isinstance(payload, dict):
        result["missing"].append("market_valuation:接口不可用")
        return result

    index_series = payload.get("index_valuation") or {}
    for code, series in index_series.items():
        if not isinstance(series, dict):
            continue
        rows, dropped, _ = _split_future(
            [{"trade_date": d, "pe_ttm": pe, "pb": pb}
             for d, pe, pb in zip(series.get("trade_dates") or [],
                                  _clean_series(series.get("pe_ttm")),
                                  _clean_series(series.get("pb")))], curr)
        if dropped:
            result["future_data_detected"] = True
            result["drops"].append(
                f"index_valuation.{code} 含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        if not rows:
            continue
        _, pe_values = _numeric_series(rows, "pe_ttm")
        _, pb_values = _numeric_series(rows, "pb")
        pe_clean = _compact(pe_values)
        pb_clean = _compact(pb_values)
        entry = {
            "name": INDEX_DISPLAY_NAMES.get(code, code),
            "pe_ttm_latest": _round(pe_clean[-1]) if pe_clean else None,
            "pb_latest": _round(pb_clean[-1]) if pb_clean else None,
            "pe_ttm_pct": _pct_rank(pe_clean, pe_clean[-1]) if pe_clean else None,
            "pb_pct": _pct_rank(pb_clean, pb_clean[-1]) if pb_clean else None,
            "samples": len(pe_clean),
            "sufficient": len(pe_clean) >= VALUATION_MIN_SAMPLES,
            # 起止日取截断后实际行（provider 声明的 first/last_date 可能含未来行）
            "first_date": normalize_date(rows[0].get("trade_date")),
            "last_date": normalize_date(rows[-1].get("trade_date")),
        }
        result["index"][code] = entry

    all_a = payload.get("all_a_snapshot") or {}
    if isinstance(all_a, dict) and all_a:
        result["all_a"] = {
            "trade_date": normalize_date(all_a.get("trade_date")),
            "pe_ttm_median": _round(_to_float(all_a.get("pe_ttm_median"))),
            "pb_median": _round(_to_float(all_a.get("pb_median"))),
            "rows": all_a.get("rows"),
            "note": "全 A 首期仅单日快照中位数（5 年分位需逐日单日拉取）",
        }

    primary = None
    for code in ("000300.SH", "000001.SH", "399001.SZ"):
        entry = result["index"].get(code)
        if entry and entry.get("sufficient"):
            primary = code
            break
    if primary is None:
        for code, entry in result["index"].items():
            if entry.get("sufficient"):
                primary = code
                break
    result["primary"] = primary
    if primary is None:
        result["missing"].append("指数 PE/PB 分位样本不足（< %d）" % VALUATION_MIN_SAMPLES)
    else:
        pct = result["index"][primary].get("pe_ttm_pct")
        if pct is None:
            pct = result["index"][primary].get("pb_pct")
        if pct is None:
            result["missing"].append("分位不可计算")
        elif pct < VALUATION_PCT_LOW:
            result["level"] = "低估"
        elif pct > VALUATION_PCT_HIGH:
            result["level"] = "高估"
        else:
            result["level"] = "合理"

    dates = [e.get("last_date") for e in result["index"].values() if e.get("last_date")]
    if dates:
        result["as_of_date"] = max(dates)
    result["notes"] = payload.get("notes") or []
    if payload.get("missing"):
        result["missing"].extend(
            f"{k}:{v}" for k, v in (payload.get("missing") or {}).items())
    return result


def _build_liquidity_metrics(payload: Optional[dict], curr: str) -> dict:
    """流动性特征：Shibor/LPR/M1·M2 方向（10Y/DR007 无接口，降级显式标注）。"""
    result = {
        "as_of_date": None,
        "direction": "信息不足",
        "shibor": {},
        "lpr": {},
        "money_supply": {},
        "missing": [],
        "notes": [],
        "drops": [],
        "future_data_detected": False,
    }
    if not isinstance(payload, dict):
        result["missing"].append("cn_liquidity_indicators:接口不可用")
        return result

    shibor = payload.get("shibor") or {}
    if shibor:
        rows = [{"trade_date": d, "on": o, "w1": w, "m1": m, "m3": m3, "y1": y}
                for d, o, w, m, m3, y in zip(
                    shibor.get("trade_dates") or [],
                    _clean_series(shibor.get("on")),
                    _clean_series(shibor.get("1w")),
                    _clean_series(shibor.get("1m")),
                    _clean_series(shibor.get("3m")),
                    _clean_series(shibor.get("1y")),
                )]
        rows, dropped, _ = _split_future(rows, curr)
        if dropped:  # 未来行丢弃事实必须显式标注（评审 m13 残留，与 money_supply 同模式）
            result["future_data_detected"] = True
            result["drops"].append(
                f"Shibor 序列含 {dropped} 条晚于分析日 {curr} 的数据，已丢弃")
        if rows:
            result["shibor"] = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "on": _series_metrics(rows, "on", (5, 20), "%"),
                "w1": _series_metrics(rows, "w1", (5, 20), "%"),
                "m3": _series_metrics(rows, "m3", (5, 20), "%"),
                "y1": _series_metrics(rows, "y1", (5, 20), "%"),
            }
            result["as_of_date"] = rows[-1].get("trade_date")

    lpr = payload.get("lpr") or {}
    if lpr:
        rows, dropped, _ = _split_future(
            [{"trade_date": d, "y1": y, "y5": y5} for d, y, y5 in zip(
                lpr.get("trade_dates") or [],
                _clean_series(lpr.get("1y")),
                _clean_series(lpr.get("5y")),
            )], curr)
        if dropped:  # 未来行丢弃事实必须显式标注（评审 m13 残留，与 money_supply 同模式）
            result["future_data_detected"] = True
            result["drops"].append(
                f"LPR 序列含 {dropped} 条晚于分析日 {curr} 的数据，已丢弃")
        if rows:
            result["lpr"] = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "y1": _series_metrics(rows, "y1", (1, 5), "%"),
                "y5": _series_metrics(rows, "y5", (1, 5), "%"),
            }

    ms = payload.get("money_supply") or {}
    if ms:
        # 月份为 YYYYMM 口径（非日期），按字符串截断到分析月
        curr_ym = curr.replace("-", "")[:6] if curr else ""
        rows = []
        for m, a, b, c, e in zip(
            ms.get("months") or [],
            _clean_series(ms.get("m1_yoy")),
            _clean_series(ms.get("m2_yoy")),
            _clean_series(ms.get("m1_mom")),
            _clean_series(ms.get("m2_mom")),
        ):
            m_str = str(m or "").strip()
            if len(m_str) == 6 and m_str.isdigit():
                if curr_ym and m_str > curr_ym:
                    result["future_data_detected"] = True
                    result["drops"].append(
                        f"货币供应 {m_str} 晚于分析月 {curr_ym}，已丢弃")
                    continue
            rows.append({"month": m_str, "m1_yoy": a, "m2_yoy": b,
                         "m1_mom": c, "m2_mom": e})
        if rows:
            result["money_supply"] = {
                "as_of_month": rows[-1].get("month"),
                "samples": len(rows),
                "m1_yoy": _series_metrics(rows, "m1_yoy", (1, 3), "%"),
                "m2_yoy": _series_metrics(rows, "m2_yoy", (1, 3), "%"),
            }

    # 方向判定：短端利率下行 + M2 同比回升 → 偏松；反向 → 偏紧；无有效输入 → 信息不足
    signals, observed = [], 0
    w1_change = _metric_value((result["shibor"].get("w1") or {}).get("change") or {}, 5)
    if w1_change is not None:
        observed += 1
        # Shibor 序列单位为 %，`change` 是百分点差 → 换算为 bp 再与阈值比较
        w1_change_bp = w1_change * 100.0
        if w1_change_bp <= -LIQUIDITY_RATE_CHANGE_BP:
            signals.append(1)
        elif w1_change_bp >= LIQUIDITY_RATE_CHANGE_BP:
            signals.append(-1)
    m2_change = _metric_value(
        (result["money_supply"].get("m2_yoy") or {}).get("change") or {}, 1)
    if m2_change is not None:
        observed += 1
        if m2_change >= LIQUIDITY_M2_CHANGE_PCT:
            signals.append(1)
        elif m2_change <= -LIQUIDITY_M2_CHANGE_PCT:
            signals.append(-1)
    if observed:
        total = sum(signals)
        result["direction"] = "偏松" if total >= 1 else ("偏紧" if total <= -1 else "中性")

    result["rate_10y"] = None
    result["dr007"] = None
    result["missing"].extend([
        "rate_10y: yc_cb 无权限 / yield_curve 接口名无效（POC）",
        "dr007: 无接口，用 Shibor 1W 替代（非 DR007）",
    ])
    if payload.get("missing"):
        result["missing"].extend(
            f"{k}:{v}" for k, v in (payload.get("missing") or {}).items())
    result["notes"] = payload.get("notes") or []
    return result


# ===========================================================================
# 特征 2：CN News 事件日历（第七章）
# ===========================================================================

def build_cn_event_calendar_features(curr_date: str,
                                    windows: Iterable[int] = DEFAULT_CALENDAR_WINDOWS) -> dict:
    """构建 CN News 资金日历特征（IPO/解禁/交割/两融/长假压力分级）。

    窗口为**交易日**口径：从 `curr_date` 起的未来 N 个交易日（含当日）。
    压力分级只使用可验证日历；金额缺失不评估（`信息不足` 并降置信度）。

    Returns:
        dict：`raw_reference` / `derived_metrics`（`windows_pressure` / `margin`）/
        `data_quality` / `future_data_detected` / `missing_inputs` / `notes`。
    """
    curr = normalize_date(curr_date)
    wds = tuple(sorted({int(w) for w in (windows or DEFAULT_CALENDAR_WINDOWS) if int(w) > 0}))
    wds = wds or DEFAULT_CALENDAR_WINDOWS
    max_w = max(wds)

    missing_inputs: list = []
    notes: list = []
    if curr is None and str(curr_date or "").strip():
        # as_of 不可解析 → `_split_future` 截断恒不生效，防前视**静默失效**（评审残留）
        notes.append(_AS_OF_UNPARSABLE_NOTE)
    future_detected = False
    actual_dates: dict = {}

    payload = _safe_call("get_cn_event_calendar", curr, windows=wds)
    raw_reference: dict = {"ipo": [], "unlocks": [], "expiry": [], "holiday_windows": [],
                           "macro_releases": []}
    if not isinstance(payload, dict):
        missing_inputs.append("cn_event_calendar")
        notes.append("事件日历不可用：仅能输出“信息不足”")
        windows_pressure = {w: _empty_pressure(w, ["事件日历接口不可用"]) for w in wds}
        return _finalize_features({
            "as_of_date": None,
            "analysis_date": curr,
            "windows": list(wds),
            "raw_reference": raw_reference,
            "derived_metrics": {"windows_pressure": windows_pressure, "margin": {}},
            "data_quality": _quality_block("insufficient", {}, missing_inputs,
                                          False, notes),
            "future_data_detected": False,
            "missing_inputs": sorted(set(missing_inputs)),
            "notes": notes,
        })

    horizon = _nth_trading_day_after(curr, max_w) or curr
    window_end = {w: (_nth_trading_day_after(curr, w) or curr) for w in wds}

    def _collect(key: str, date_key: str) -> list:
        items = []
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            d = normalize_date(item.get(date_key))
            if d is None:
                continue
            if d < curr or d > horizon:  # 只保留当前及最长窗口内的日历条目
                continue
            entry = dict(item)
            entry[date_key] = d
            items.append(entry)
        items.sort(key=lambda r: r[date_key])
        return items

    ipo_items = _collect("ipo", "list_date")
    ipo_subscribe = _collect("ipo", "subscribe_date")
    unlock_items = _collect("unlocks", "float_date")
    expiry_items = _collect("expiry", "date")
    holiday_windows = []
    for item in payload.get("holiday_windows") or []:
        if not isinstance(item, dict):
            continue
        start = normalize_date(item.get("start") or item.get("from"))
        end = normalize_date(item.get("end") or item.get("to"))
        if not start or not end:
            continue
        if end < curr or start > horizon:
            continue
        holiday_windows.append({"start": start, "end": end,
                                "days": item.get("days") or _days_inclusive(start, end)})
    holiday_windows.sort(key=lambda r: r["start"])

    raw_reference.update({
        "ipo": ipo_items + [i for i in ipo_subscribe if i not in ipo_items],
        "unlocks": unlock_items,
        "expiry": expiry_items,
        "holiday_windows": holiday_windows,
        "macro_releases": [i for i in (payload.get("macro_releases") or [])
                           if isinstance(i, dict)],
        "source": payload.get("source") or "provider",
    })
    if payload.get("as_of_date"):
        actual_dates["cn_event_calendar"] = normalize_date(payload.get("as_of_date"))
    if payload.get("missing"):
        notes.extend(f"{k}:{v}" for k, v in (payload.get("missing") or {}).items())

    # ---- 两融（历史序列，1/5/20 变化额/率） ----
    margin_payload = _safe_call("get_margin_trading_history", curr, days=max(20, 5))
    margin = {"as_of_date": None, "samples": 0}
    if isinstance(margin_payload, dict) and margin_payload.get("series"):
        rows, dropped, _ = _split_future(margin_payload.get("series") or [], curr)
        if dropped:
            future_detected = True
            notes.append(f"两融序列含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        if rows:
            margin = {
                "as_of_date": rows[-1].get("trade_date"),
                "samples": len(rows),
                "rzye": _series_metrics(rows, "rzye", (1, 5, 20), "元"),
            }
            actual_dates["margin_trading_history"] = margin["as_of_date"]
    else:
        missing_inputs.append("margin_trading_history")
        notes.append("两融序列不可用：资金压力维度缺失（不评估）")

    margin_rate, margin_rate_window = _margin_change_rate(margin)
    margin_driver = _margin_driver(margin, margin_rate, margin_rate_window)
    if margin_rate is None:
        notes.append("两融变化不可用：仅作水位参考" if margin.get("as_of_date") else "两融不可用")

    # ---- 按窗口聚合压力 ----
    windows_pressure: dict = {}
    for w in wds:
        end = window_end[w]
        ipo_w = [i for i in ipo_items if i["list_date"] <= end] + \
                [i for i in ipo_subscribe if i["subscribe_date"] <= end]
        unlock_w = [i for i in unlock_items if i["float_date"] <= end]
        expiry_w = [i for i in expiry_items if i["date"] <= end]
        holiday_w = [h for h in holiday_windows if h["start"] <= end]
        windows_pressure[w] = _aggregate_window_pressure(
            w, end, _dedupe_by_code(ipo_w), unlock_w,
            expiry_w, holiday_w, margin_driver, curr)

    derived = {
        "windows_pressure": windows_pressure,
        "margin": {
            "as_of_date": margin.get("as_of_date"),
            "samples": margin.get("samples", 0),
            "change_rate": margin_rate,
            "change_rate_window": margin_rate_window,
            "rzye_latest": (margin.get("rzye") or {}).get("latest"),
            "rzye_change": {
                w: _metric_value((margin.get("rzye") or {}).get("change") or {}, w)
                for w in (1, 5, 20)
            },
            "note": "两融为余额水位；大幅去杠杆（变化率 ≤ -%.1f%%）计入压力" % (
                MARGIN_CHANGE_RATE_HIGH * 100),
        },
    }

    status = "ok"
    if missing_inputs and len(missing_inputs) >= 2:
        status = "insufficient"
    elif missing_inputs:
        status = "partial"
    return _finalize_features({
        "as_of_date": max(actual_dates.values()) if actual_dates else None,
        "analysis_date": curr,
        "windows": list(wds),
        "raw_reference": raw_reference,
        "derived_metrics": derived,
        "data_quality": _quality_block(
            status, actual_dates, missing_inputs, future_detected, notes),
        "future_data_detected": bool(future_detected),
        "missing_inputs": sorted(set(missing_inputs)),
        "notes": notes,
    })


def _days_inclusive(start: str, end: str) -> Optional[int]:
    try:
        s = _datetime.strptime(start, "%Y-%m-%d").date()
        e = _datetime.strptime(end, "%Y-%m-%d").date()
        return (e - s).days + 1
    except Exception:
        return None


def _empty_pressure(window: int, missing: list) -> dict:
    return {
        "window": window,
        "window_end": None,
        "level": "信息不足",
        "score": None,
        "drivers": [],
        "key_dates": [],
        "confidence": "low",
        "missing": list(missing or []),
    }


def _margin_change_rate(margin: dict) -> tuple:
    """两融变化率与所用窗口：优先 20 日，其次 5 日、1 日。"""
    change = ((margin.get("rzye") or {}).get("change") or {})
    for w in (20, 5, 1):
        metric = change.get(w) or {}
        pct = metric.get("pct") or {}
        if pct.get("value") is not None:
            return _round(pct.get("value") / 100.0, 6), w
    return None, None


def _margin_driver(margin: dict, rate: Optional[float], rate_window: Optional[int]) -> Optional[dict]:
    """两融变化 → 压力子分（去杠杆为压力；仅水位时返回 None）。"""
    if rate is None:
        return None
    if rate <= -MARGIN_CHANGE_RATE_HIGH:
        score = PRESSURE_SCORE_HIGH
    elif rate <= -MARGIN_CHANGE_RATE_MEDIUM:
        score = PRESSURE_SCORE_MEDIUM
    else:
        score = PRESSURE_SCORE_MIN
    return {
        "source": "margin",
        "score": score,
        "detail": "融资余额 %s 日变化 %.2f%%（余额 %s）" % (
            rate_window, rate * 100,
            "%.0f 亿元" % (margin.get("rzye", {}).get("latest", 0) / 1e8)
            if margin.get("rzye", {}).get("latest") else "不可用"),
        "window": rate_window,
    }


def _dedupe_by_code(items: list) -> list:
    """按 `ts_code`/`name` 去重（同一项目可能同时出现在申购与上市列表）。"""
    seen, out = set(), []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get("ts_code") or item.get("name") or id(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _aggregate_window_pressure(window: int, window_end: str, ipo_w: list,
                               unlock_w: list, expiry_w: list, holiday_w: list,
                               margin_driver: Optional[dict], curr: str) -> dict:
    """单个交易日窗口的 IPO/解禁/交割/长假/两融压力聚合（阈值见文件顶部常量）。"""
    drivers, key_dates, missing = [], [], []
    sub_scores: dict = {}

    # IPO：仅募资额/发行规模可判断抽血强度
    amount_sum, amount_missing, max_item = 0.0, 0, None
    for item in ipo_w:
        amount = _to_float(item.get("market_amount"))
        if amount is None:
            amount_missing += 1
            continue
        amount_sum += amount
        if max_item is None or amount > (max_item[1] or 0):
            max_item = (item.get("name") or item.get("ts_code"), amount)
    if amount_sum > 0:
        score = (PRESSURE_SCORE_HIGH if amount_sum >= IPO_AMOUNT_HIGH_YI
                 else PRESSURE_SCORE_MEDIUM if amount_sum >= IPO_AMOUNT_MEDIUM_YI
                 else PRESSURE_SCORE_MIN)
        sub_scores["ipo"] = score
        drivers.append({
            "source": "ipo",
            "score": score,
            "detail": "窗口内 IPO 募资额合计 %.1f 亿元（%d 家；最大 %s %.1f 亿元）" % (
                amount_sum, len([i for i in ipo_w]),
                max_item[0] if max_item else "-", max_item[1] if max_item else 0.0),
            "amount_yi": _round(amount_sum, 2),
        })
    else:
        missing.append("ipo:募资额缺失，不可评估抽血强度" if ipo_w else "ipo:窗口内无项目")
    if amount_missing:
        missing.append(f"ipo:{amount_missing} 个项目缺金额（不评估）")

    # 解禁：优先市值/比例；无比例时输出数量并降置信度
    ratio_max, ratio_sum, ratio_missing = None, 0.0, 0
    for item in unlock_w:
        ratio = _to_float(item.get("float_ratio"))
        if ratio is None:
            ratio_missing += 1
            continue
        ratio_val = ratio / 100.0 if ratio > 1 else ratio
        ratio_max = ratio_val if ratio_max is None else max(ratio_max, ratio_val)
        ratio_sum += ratio_val
    count = len(unlock_w)
    unlock_signals = []
    if ratio_max is not None:
        unlock_signals.append(
            PRESSURE_SCORE_HIGH if ratio_max >= UNLOCK_RATIO_MAX_HIGH
            else PRESSURE_SCORE_MEDIUM if ratio_max >= UNLOCK_RATIO_MAX_MEDIUM
            else PRESSURE_SCORE_MIN)
    if ratio_sum > 0:
        unlock_signals.append(
            PRESSURE_SCORE_HIGH if ratio_sum >= UNLOCK_WINDOW_SUM_HIGH
            else PRESSURE_SCORE_MEDIUM if ratio_sum >= UNLOCK_WINDOW_SUM_MEDIUM
            else PRESSURE_SCORE_MIN)
    unlock_signals.append(
        PRESSURE_SCORE_MEDIUM if count >= UNLOCK_COUNT_HIGH
        else PRESSURE_SCORE_LOW if count >= UNLOCK_COUNT_MEDIUM
        else PRESSURE_SCORE_MIN)
    if count:
        score = max(unlock_signals)
        sub_scores["unlock"] = score
        drivers.append({
            "source": "unlock",
            "score": score,
            "detail": "窗口内解禁 %d 家；单只最大占流通 %.2f%%；合计占比 %.2f%%" % (
                count,
                (ratio_max or 0) * 100,
                ratio_sum * 100),
            "count": count,
            "ratio_max": _round(ratio_max, 4) if ratio_max is not None else None,
            "ratio_sum": _round(ratio_sum, 4) if ratio_sum else None,
        })
        if ratio_missing:
            missing.append(f"unlock:{ratio_missing} 条缺比例（按家数评估并降置信度）")
        peak = max(unlock_w, key=lambda r: _to_float(r.get("float_ratio")) or 0)
        key_dates.append({
            "date": peak.get("float_date"),
            "source": "unlock",
            "detail": "解禁峰值：%s（占流通 %s）" % (
                peak.get("name") or peak.get("ts_code"),
                ("%.2f%%" % (_to_float(peak.get("float_ratio")) or 0))
                if _to_float(peak.get("float_ratio")) is not None else "比例缺失"),
        })
    else:
        missing.append("unlock:窗口内无解禁")

    # 交割：规则日历结构化
    if expiry_w:
        nearest = expiry_w[0]
        days_to = _trading_days_between(curr, nearest.get("date"))
        score = PRESSURE_SCORE_MIN
        if days_to is not None:
            if days_to <= EXPIRY_NEAR_TRADING_DAYS:
                score = PRESSURE_SCORE_MEDIUM
            elif days_to <= EXPIRY_MID_TRADING_DAYS:
                score = PRESSURE_SCORE_LOW
        sub_scores["expiry"] = score
        drivers.append({
            "source": "expiry",
            "score": score,
            "detail": "最近交割日 %s（%s，距 %s 个交易日）" % (
                nearest.get("date"), nearest.get("kind") or "股指期货",
                days_to if days_to is not None else "?"),
        })
        key_dates.append({"date": nearest.get("date"), "source": "expiry",
                          "detail": "交割日：%s" % (nearest.get("kind") or "股指期货")})

    # 长假：可验证日历
    if holiday_w:
        first = holiday_w[0]
        sub_scores.setdefault("holiday", PRESSURE_SCORE_LOW)
        drivers.append({
            "source": "holiday",
            "score": PRESSURE_SCORE_LOW,
            "detail": "窗口内长假：%s ~ %s（%s 天）" % (
                first.get("start"), first.get("end"), first.get("days")),
        })
        key_dates.append({"date": first.get("start"), "source": "holiday",
                          "detail": "长假休市起点"})

    # 两融
    if margin_driver:
        sub_scores["margin"] = margin_driver["score"]
        drivers.append(dict(margin_driver))

    key_dates.sort(key=lambda r: r.get("date") or "")
    if not sub_scores:
        return {
            "window": window,
            "window_end": window_end,
            "level": "信息不足",
            "score": None,
            "drivers": drivers,
            "key_dates": key_dates,
            "confidence": "low",
            "missing": missing,
        }
    score = max(sub_scores.values())
    available = [k for k, v in sub_scores.items() if v is not None]
    confidence = "high" if len(available) >= 4 else ("medium" if len(available) >= 2 else "low")
    return {
        "window": window,
        "window_end": window_end,
        "level": _level_from_score(score),
        "score": score,
        "drivers": drivers,
        "key_dates": key_dates,
        "confidence": confidence,
        "missing": missing,
    }


# ===========================================================================
# 特征 3：全球风险（第六、十章）
# ===========================================================================

def build_global_risk_features(curr_date: str,
                               lookbacks: Iterable[int] = DEFAULT_GLOBAL_LOOKBACKS) -> dict:
    """构建全球风险价格特征（美债/美股/波动率/汇率/商品），供国际影响节点消费。

    POC 实测：无 VIX/SOX/美元指数代码 → 用已实现波动率与 USDCNH 替代；
    商品用国内 `fut_daily`（标注“国内价、非 WTI/伦金”）。

    Returns:
        dict：`raw_reference` / `derived_metrics`（分组价格证据 + `coverage`）/
        `data_quality` / `future_data_detected` / `missing_inputs` / `notes`。
    """
    curr = normalize_date(curr_date)
    lbs = tuple(sorted({int(w) for w in (lookbacks or DEFAULT_GLOBAL_LOOKBACKS) if int(w) > 0}))
    lbs = lbs or DEFAULT_GLOBAL_LOOKBACKS
    days = max(lbs)

    missing_inputs: list = []
    notes: list = []
    if curr is None and str(curr_date or "").strip():
        # as_of 不可解析 → `_split_future` 截断恒不生效，防前视**静默失效**（评审残留）
        notes.append(_AS_OF_UNPARSABLE_NOTE)
    future_detected = False
    actual_dates: dict = {}
    raw_reference: dict = {}
    derived: dict = {}

    payload = _safe_call("get_global_risk_indicators", curr, days=days)

    def _group_series(key: str, windows: tuple) -> dict:
        """从 payload 的分组里派生序列指标（带未来数据截断）。"""
        nonlocal future_detected
        series = (payload or {}).get(key) or {}
        out = {}
        for code, entry in series.items():
            if not isinstance(entry, dict):
                continue
            field = entry.get("field") or _default_field(key)
            rows, dropped, _ = _split_future(
                [{"trade_date": d, "v": v} for d, v in
                 zip(entry.get("trade_dates") or [], _clean_series(entry.get(field)))], curr)
            if dropped:
                future_detected = True
                notes.append(f"{key}.{code} 含 {dropped} 条晚于 {curr} 的数据，已丢弃")
            rows = [r for r in rows if r.get("v") is not None]
            if not rows:
                continue
            metrics = _series_metrics(rows, "v", windows, entry.get("unit") or "")
            metrics["field"] = field
            metrics["source"] = entry.get("source")
            metrics["note"] = entry.get("note")
            out[code] = metrics
            if metrics.get("as_of_date"):
                as_of_candidates.append(metrics["as_of_date"])
        return out

    as_of_candidates: list = []

    if not isinstance(payload, dict):
        missing_inputs.append("global_risk_indicators")
        notes.append("全球风险价格不可用：全部证据缺失（门控上限 caution）")
        coverage = {"available": [], "missing": list(GLOBAL_RISK_CORE_ITEMS),
                    "ratio": 0.0, "status": "insufficient"}
        return _finalize_features({
            "as_of_date": None,
            "analysis_date": curr,
            "lookbacks": list(lbs),
            "raw_reference": raw_reference,
            "derived_metrics": {"coverage": coverage},
            "data_quality": _quality_block("insufficient", {}, missing_inputs,
                                          False, notes),
            "future_data_detected": False,
            "missing_inputs": sorted(set(missing_inputs)),
            "notes": notes,
        })

    us_rates = _group_series("us_treasury", (1, 5, 20))
    real_yield = _group_series("us_real_yield", (5, 20))
    long_rate = _group_series("us_long_rate", (5, 20))
    equities = _group_series("global_indices", (5, 20))
    fx = _group_series("fx", (1, 5, 20))
    commodities = _group_series("commodities", (5, 20))

    # 期限利差（10Y-2Y）
    term_spread = None
    y10 = us_rates.get("y10") or {}
    y2 = us_rates.get("y2") or {}
    if y10.get("latest") is not None and y2.get("latest") is not None:
        spread_change = {}
        for w in (1, 5, 20):
            c10 = _metric_value(y10.get("change") or {}, w)
            c2 = _metric_value(y2.get("change") or {}, w)
            spread_change[w] = {
                "value": _round(c10 - c2) if (c10 is not None and c2 is not None) else None,
                "window": w,
                "samples": min((y10.get("change", {}).get(w) or {}).get("samples", 0),
                               (y2.get("change", {}).get(w) or {}).get("samples", 0)),
                "sufficient": c10 is not None and c2 is not None,
                "unit": "pp",
            }
        term_spread = {
            "latest": _round(y10["latest"] - y2["latest"]),
            "as_of_date": min(x for x in (y10.get("as_of_date"), y2.get("as_of_date")) if x),
            "samples": min(y10.get("samples", 0), y2.get("samples", 0)),
            "change": spread_change,
            "unit": "pp",
            "note": "10Y-2Y；负值 = 倒挂",
        }
        if y10.get("as_of_date"):
            as_of_candidates.append(y10["as_of_date"])

    # 波动率：美股指数已实现波动率（替代 VIX，标注方法）
    vol_candidates = {}
    for code in ("SPX", "IXIC"):
        entry = equities.get(code) or {}
        if not entry:
            continue
        # 未来数据截断（评审 m13）：与 `_group_series` 同口径，晚于分析日的收盘价不入
        # 已实现波动率窗口（此前直接回读原始序列，未来行会污染尾部窗口）
        rows, dropped, _ = _split_future(
            [{"trade_date": d, "v": v} for d, v in
             zip((((payload.get("global_indices") or {}).get(code) or {}).get("trade_dates")
                  or []),
                 _clean_series(((payload.get("global_indices") or {}).get(code) or {})
                               .get("close")))], curr)
        if dropped:
            future_detected = True
            notes.append(f"global_indices.{code} 含 {dropped} 条晚于 {curr} 的数据，已丢弃")
        _, values = _numeric_series(rows, "v")
        clean = _compact(values)
        if len(clean) >= REALIZED_VOL_MIN_SAMPLES:
            vol = _realized_vol_pct(clean[-REALIZED_VOL_MIN_SAMPLES:])
            if vol is not None:
                vol_candidates[code] = vol
    volatility = {}
    if vol_candidates:
        primary_vol_code = "SPX" if "SPX" in vol_candidates else next(iter(vol_candidates))
        volatility = {
            "value": vol_candidates[primary_vol_code],
            "values": vol_candidates,
            "method": "20 日已实现波动率（年化，%）",
            "proxy_for": "VIX",
            "note": "index_global 无 VIX 代码（POC），以已实现波动率替代",
            "samples": REALIZED_VOL_MIN_SAMPLES,
            "sufficient": True,
        }

    derived.update({
        "us_rates": {code: entry for code, entry in us_rates.items()},
        "us_real_yield": real_yield,
        "us_long_rate": long_rate,
        "term_spread": term_spread,
        "equities": equities,
        "volatility": volatility,
        "fx": fx,
        "commodities": commodities,
    })

    # 收益率（各 lookback）
    for group in (equities, fx, commodities):
        for code, entry in group.items():
            series_returns = {}
            for w in lbs:
                series_returns[w] = entry.get("change", {}).get(w, {}).get("pct", {})
            entry["return"] = series_returns

    # 核心覆盖度
    available, core_missing = [], []
    checks = {
        "us_10y": y10.get("latest") is not None,
        "term_spread": bool(term_spread),
        "equity_us": bool(equities.get("SPX") or equities.get("IXIC")),
        "volatility": bool(volatility),
        "fx_usdcnh": bool(fx.get("USDCNH.FXCM") or fx.get("USDCNH")),
        "commodity_cn": bool(commodities),
    }
    for item in GLOBAL_RISK_CORE_ITEMS:
        (available if checks.get(item) else core_missing).append(item)
    ratio = round(len(available) / len(GLOBAL_RISK_CORE_ITEMS), 4)
    coverage = {
        "available": available,
        "missing": core_missing,
        "ratio": ratio,
        "status": ("insufficient" if ratio < GLOBAL_RISK_CORE_MIN_RATIO
                   else ("ok" if not core_missing else "partial")),
        "min_ratio": GLOBAL_RISK_CORE_MIN_RATIO,
    }
    derived["coverage"] = coverage

    if payload.get("missing"):
        for k, v in (payload.get("missing") or {}).items():
            notes.append(f"{k}:{v}")
    notes.extend(payload.get("notes") or [])
    for item in core_missing:
        missing_inputs.append(f"global_risk:{item}")
    if payload.get("as_of_date"):
        actual_dates["global_risk_indicators"] = normalize_date(payload.get("as_of_date"))

    status = "ok"
    if coverage["status"] == "insufficient":
        status = "insufficient"
    elif coverage["status"] == "partial" or missing_inputs:
        status = "partial"

    return _finalize_features({
        "as_of_date": min(as_of_candidates) if as_of_candidates else None,
        "analysis_date": curr,
        "lookbacks": list(lbs),
        "raw_reference": {
            "series": {
                key: {code: {"trade_dates": (entry or {}).get("trade_dates"),
                             "field": (entry or {}).get("field"),
                             "source": (entry or {}).get("source")}
                      for code, entry in ((payload.get(key) or {}).items())}
                for key in ("us_treasury", "global_indices", "fx", "commodities")
            },
        },
        "derived_metrics": derived,
        "data_quality": _quality_block(
            status, actual_dates, missing_inputs, future_detected, notes),
        "future_data_detected": bool(future_detected),
        "missing_inputs": sorted(set(missing_inputs)),
        "notes": notes,
    })


def _default_field(group: str) -> str:
    """各分组默认取值字段（Provider 契约；POC 实测字段名）。"""
    return {
        "us_treasury": "close",
        "us_real_yield": "close",
        "us_long_rate": "close",
        "global_indices": "close",
        "fx": "bid_close",
        "commodities": "close",
    }.get(group, "close")


# ===========================================================================
# 风险门控（第十二章有序规则表）
# ===========================================================================

def _regime_level(regime: Any, key: str, enum: tuple) -> Optional[str]:
    """读取 `market_regime[key]["level"]` 并校验枚举（非法/缺失返回 `None`）。"""
    entry = (regime or {}).get(key) if isinstance(regime, dict) else None
    if isinstance(entry, dict):
        level = entry.get("level")
    elif isinstance(entry, str):
        level = entry  # 容忍简写；T6 直接替换后应始终为 dict
    else:
        level = None
    if isinstance(level, str):
        level = level.strip()
        if level in enum:
            return level
    return None


def _node_degradation(node_output: Any) -> Optional[str]:
    """读取节点输出内嵌的 `data_quality.degradation_level`（无标注返回 `None`）。"""
    if not isinstance(node_output, dict):
        return None
    dq = node_output.get("data_quality")
    if not isinstance(dq, dict):
        return None
    level = dq.get("degradation_level") or dq.get("status")
    if isinstance(level, str) and level in DEGRADATION_LEVEL_ENUM:
        return level
    return None


def _snapshot_core_insufficient(snapshot: Any) -> bool:
    """判据 (a)：启动快照中核心输入缺失/不足（或已实测条目缺实际日期）。"""
    if not isinstance(snapshot, dict) or not snapshot:
        return True  # 无快照（图启动未写入）→ 视为不足（fail-closed → caution）
    if snapshot.get("degradation_level") == "insufficient":
        return True
    datasets = snapshot.get("datasets")
    if isinstance(datasets, dict) and datasets:
        for entry in datasets.values():
            if not isinstance(entry, dict) or not entry.get("core"):
                continue
            if entry.get("status") in CORE_MISSING_STATUSES:
                return True
            if entry.get("measured") and not entry.get("actual_date"):
                return True
        return False
    if snapshot.get("core_insufficient") is not None:
        return bool(snapshot.get("core_insufficient"))
    return True


def _core_quality_insufficient(snapshot: Any, gra: Any, regime: Any) -> bool:
    """判据 (a)+(b)：启动快照不足 **或** 节点级不足 → True（并集，评审 M2）。

    节点自评**不得覆盖**启动快照——此前实现里节点自报 `ok` 会把快照的
    `insufficient` 清零（判据 (a) 被绕过），核心输入缺失时规则 3 全局上限失效。
    节点未标注级别（`_node_degradation` 返回 None）时按快照为准（保守）。
    """
    if _snapshot_core_insufficient(snapshot):
        return True
    for node_output in (gra, regime):
        if _node_degradation(node_output) == "insufficient":
            return True
    return False


def risk_gate_decision(global_risk_assessment: Any = None,
                       market_regime: Any = None,
                       market_data_quality: Any = None) -> dict:
    """有序规则表完整判定（含命中规则与原因，便于日志与单测断言）。

    Returns:
        `{"gate", "rule", "reason"}`；`rule` 为 1-5 的规则序号。
    """
    gra = global_risk_assessment if isinstance(global_risk_assessment, dict) else {}
    regime = market_regime if isinstance(market_regime, dict) else {}

    systemic = gra.get("systemic_risk")
    if isinstance(systemic, str):
        systemic = systemic.strip()
    # 置信度归一（别名 + 字符串数字；评审 M3）——不可解析为 None，进判据 (c)
    confidence = _norm_confidence(gra.get("confidence"))
    short = _regime_level(regime, "short_term", SHORT_TERM_ENUM)
    wave = _regime_level(regime, "wave", WAVE_ENUM)

    # 判据 (c)：结构解析失败（必需枚举字段缺失/不可解析）
    # - confidence 不在「不低于中」集合（含缺失/不可解析/低）→ 不得落规则 5 normal
    #   （systemic_risk=high + confidence 缺失此前直落 normal，评审 M3）
    # - wave 缺失同纳入判据 (c)（评审 M3）
    structure_failed = (
        (systemic not in SYSTEMIC_RISK_ENUM)
        or (short is None)
        or (wave is None)
        or (confidence not in CONFIDENCE_NOT_BELOW_MEDIUM)
    )
    # 判据 (d)：输出级不足枚举
    output_insufficient = (systemic == "insufficient") or (short == "信息不足")
    # 判据 (a)+(b)：核心质量不足
    quality_insufficient = _core_quality_insufficient(
        market_data_quality, gra, regime)

    if output_insufficient and quality_insufficient is False:
        dq_missing = [name for name, node in (("global_risk_assessment", gra),
                                              ("market_regime", regime))
                      if _node_degradation(node) != "insufficient"]
        if not structure_failed and dq_missing:
            # 契约违约（节点输出不足枚举但子字段未标注 insufficient）：门控仍按规则 3
            logger.warning(
                "[market_features] 输出级不足枚举但 data_quality 未标注 insufficient（违约写实现错误）: %s",
                "、".join(dq_missing))

    if not (structure_failed or output_insufficient or quality_insufficient):
        if systemic == "high" and confidence in CONFIDENCE_NOT_BELOW_MEDIUM:
            return {"gate": "block", "rule": 1,
                    "reason": f"systemic_risk=high 且 confidence={confidence}"}
        if short == "回避":
            return {"gate": "block", "rule": 2, "reason": "market_regime 短线=回避"}

    if quality_insufficient:
        return {"gate": RISK_GATE_FALLBACK, "rule": 3,
                "reason": "核心质量不足（判据 a/b：启动快照或节点级 data_quality）"}
    if structure_failed:
        failed_fields = []
        if systemic not in SYSTEMIC_RISK_ENUM:
            failed_fields.append(f"systemic_risk={systemic!r}")
        if short is None:
            failed_fields.append("短线级别")
        if wave is None:
            failed_fields.append("波段级别")
        if confidence not in CONFIDENCE_NOT_BELOW_MEDIUM:
            failed_fields.append(f"confidence={confidence!r}")
        return {"gate": RISK_GATE_FALLBACK, "rule": 3,
                "reason": "结构解析失败/必需枚举字段缺失（判据 c）："
                          + "、".join(failed_fields)}
    if output_insufficient:
        return {"gate": RISK_GATE_FALLBACK, "rule": 3,
                "reason": "输出级不足枚举（判据 d）"}

    if systemic == "medium" or short == "谨慎" or wave == "防御":
        return {"gate": "caution", "rule": 4,
                "reason": f"systemic_risk={systemic}、短线={short}、波段={wave}"}
    return {"gate": "normal", "rule": 5, "reason": "无命中规则"}


def derive_risk_gate(global_risk_assessment: Any = None,
                     market_regime: Any = None,
                     market_data_quality: Any = None) -> str:
    """按第十二章有序规则表（短路判定）派生 `risk_gate`。

    规则（逐条命中即返回）：
      1. `systemic_risk=high` 且 `confidence ∈ {high, medium}` 且核心质量非不足 → `block`
      2. `market_regime` 短线 = 回避 且核心质量非不足 → `block`
      3. 核心质量不足（判据 (a)–(d)，见 `risk_gate_decision`） → `caution`（全局上限）
      4. `systemic_risk=medium` 或短线 = 谨慎 或波段 = 防御 → `caution`
      5. 其余 → `normal`

    关键数据不足（含结构解析失败）由 fail-open 改为 `caution`（已确认决策）。
    判据 (c) 含 `confidence` 缺失/不可解析/低于中（评审 M3）与 `wave` 缺失——
    两类均落规则 3 `caution`，不得落规则 5 `normal`。
    """
    return risk_gate_decision(global_risk_assessment, market_regime,
                              market_data_quality)["gate"]


# ===========================================================================
# 结构化输出解析（LLM 报告 → dict，解析失败降级“信息不足”）
# ===========================================================================

_LEVEL_KEYS = {
    "short_term": ("short_term", "短线", "短线(5日)", "短线（5日）"),
    "wave": ("wave", "波段", "波段(20日)", "波段（20日）"),
    "long_term": ("long_term", "long_term_level", "长线", "长线(60日)", "长线（60日）"),
}
_CONFIDENCE_ALIASES = {
    "high": "high", "medium": "medium", "low": "low",
    "高": "high", "中": "medium", "低": "low",
}


def _extract_json_object(text: Any) -> Optional[dict]:
    """从报告文本中提取首个 JSON 对象（代码块 → 全文 → 括号配平扫描）。"""
    if not isinstance(text, str) or "{" not in text:
        return None
    import json
    import re

    candidates = [m.group(1) for m in re.finditer(
        r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)]
    candidates.append(text)
    # 括号配平扫描（避开字符串内的花括号）
    start = text.find("{")
    if start >= 0:
        depth, in_str, escape = 0, False, False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:idx + 1])
                    break
    for cand in candidates:
        cand = (cand or "").strip()
        if not cand.startswith("{"):
            continue
        try:
            data = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            for wrapper in ("market_regime", "market_event_calendar", "data", "result"):
                inner = data.get(wrapper)
                if isinstance(inner, dict) and len(data) <= 3:
                    return inner
            return data
    return None


def _first_key(data: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


# 中文否定识别（评审 M1 + Code Review 第 2 轮 findings 1/2）：
# 第 1 版「回看 1–2 字 ∈ 不非未无没」存在双向缺陷——假否定（「非常/无非/未来」
# 等含否定字的复合词被当否定 → 真 high 漏熔断）与漏否定（「并没有偏高」等
# 多字否定+填充字形态未识别 → 反配误熔断）。本版按「复合词白名单 → 剥一个
# 填充字 → 否定模式表 → 双重否定抵消」设计，回归用例见
# tests/dataflows/test_market_features.py::test_chinese_negation_patterns。
_NON_NEGATION_COMPOUNDS = (
    "非常", "无非", "无比", "未来", "未曾", "未遂", "未尝", "未有", "未定", "未免", "无效", "无疑",
)
_NEGATION_PATTERNS = (
    "并不", "并非", "没有", "尚未", "从未", "不算", "不太", "谈不上",
    "不", "未", "无", "没", "非",
)
_NEGATION_FILLERS = ("有", "是", "算", "偏", "太", "点", "些", "再", "会")
_DOUBLE_NEGATION_CHARS = "不非未无没"


def _strip_negation_filler(text: str) -> str:
    """剥掉否定模式与 token 之间的一个填充字（并没有偏高 → 并没有 + 高）。"""
    for filler in _NEGATION_FILLERS:
        if text.endswith(filler):
            return text[:-1]
    return text


def _negated_at(text: str, index: int) -> bool:
    """`text[index]` 命中 token 时，其前方紧邻片段是否为否定式。

    规则：
    1. 复合词白名单（含否定字但不是否定：非常/无非/未来…）直接判非否定；
    2. 剥掉一个填充字（有/是/算/偏/太…）后匹配否定模式（并没有偏高 → 并不）；
    3. 模式前一字若也是否定字 → 双重否定抵消（并非不高 → 非否定）。
    """
    prefix = text[max(0, index - 8):index]
    for core in (prefix, _strip_negation_filler(prefix)):
        for comp in _NON_NEGATION_COMPOUNDS:
            if core.endswith(comp):
                return False
    core = _strip_negation_filler(prefix)
    for pat in _NEGATION_PATTERNS:
        if core.endswith(pat):
            before = core[:len(core) - len(pat)]
            if before and before[-1] in _DOUBLE_NEGATION_CHARS:
                return False
            return True
    return False


def _substring_token(text: str, tokens: Iterable[str]) -> Optional[str]:
    """中文 token 子串命中（跳过 ascii token 与**否定式命中**）；无命中返回 None。

    同一 token 多次出现时逐次判定：「不高」不返回，继续找下一个非否定出现
    （「不高，但尾部风险高」→ 高）；全部为否定式则视为未命中（调用方降级
    「信息不足」/None 走解析失败路径，评审 M1）。
    """
    for token in tokens:
        if token.isascii():
            continue
        start = text.find(token)
        while start >= 0:
            if not _negated_at(text, start):
                return token
            start = text.find(token, start + 1)
    return None


def _norm_enum_field(data: dict, keys: Iterable[str], enum: tuple,
                     default: str = "信息不足") -> str:
    """取值为固定枚举；非法/缺失/否定式（「不高」）降级为 `default`。"""
    value = _first_key(data, keys)
    if isinstance(value, str):
        value = value.strip()
        if value in enum:
            return value
        token = _substring_token(value, [t for t in enum if t != "信息不足"])
        if token:
            return token
    return default


def _confidence_from_value(value: Any) -> Optional[str]:
    """数值/字符串数字置信度 → 枚举（≥0.7 high / ≥0.4 medium / 其余 low）。

    支持小数（0.85）与百分数写法（85 / "85%"）；不可解析返回 None。
    """
    text = str(value).strip().rstrip("%％").strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number > 1.0:  # LLM 常见的百分数写法（85 → 0.85）
        number = number / 100.0
    if number >= 0.7:
        return "high"
    if number >= 0.4:
        return "medium"
    return "low"


def _norm_confidence(value: Any) -> Optional[str]:
    """置信度 → high/medium/low；别名未命中且不可解析返回 None（评审 M3）。

    str 别名（高/中/低/high/…，否定式「不高」不匹配别名）与**字符串数字**
    （"0.85" / "85%"）均解析——字符串数字此前不可解析，导致规则 1 被跳过、
    整体落规则 5 `normal`（门控漏洞）。
    """
    if isinstance(value, str):
        text = value.strip()
        return (_CONFIDENCE_ALIASES.get(text.lower())
                or _CONFIDENCE_ALIASES.get(text)
                or _confidence_from_value(text))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _confidence_from_value(value)
    return None


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "；".join(_norm_text(v) for v in value if v is not None)
    if isinstance(value, dict):
        return "；".join(f"{k}: {_norm_text(v)}" for k, v in value.items())
    return str(value)


def _norm_level_entry(data: Any, keys: Iterable[str], enum: tuple,
                      default_confidence: Optional[str] = None) -> dict:
    """级别条目归一：`{level, evidence, confirm, invalidate, confidence}`。"""
    raw = _first_key(data, keys) if isinstance(data, dict) else None
    entry = {"level": "信息不足", "evidence": "", "confirm": "", "invalidate": "",
             "confidence": default_confidence}
    if isinstance(raw, str):
        token = _substring_token(raw, [c for c in enum if c != "信息不足"])
        entry["level"] = token or "信息不足"
        entry["evidence"] = raw.strip()
        return entry
    if not isinstance(raw, dict):
        return entry
    level = raw.get("level") or raw.get("结论") or raw.get("判定")
    if isinstance(level, str):
        level = level.strip()
        if level in enum:
            entry["level"] = level
        else:
            token = _substring_token(level, [c for c in enum if c != "信息不足"])
            if token:
                entry["level"] = token
    entry["evidence"] = _norm_text(
        raw.get("evidence") or raw.get("证据") or raw.get("reason") or raw.get("理由"))
    entry["confirm"] = _norm_text(
        raw.get("confirm") or raw.get("confirmed") or raw.get("确认条件"))
    entry["invalidate"] = _norm_text(
        raw.get("invalidate") or raw.get("invalidated") or raw.get("失效条件"))
    entry["confidence"] = _norm_confidence(raw.get("confidence") or raw.get("置信度")) \
        or entry["confidence"]
    return entry


def _legacy_level_from_text(text: str, keys: Iterable[str], enum: tuple) -> Optional[dict]:
    """从旧结论文本中提取级别（形如“短线: 谨慎 建议仓位 3 成 — 理由”）。"""
    import re

    for key in keys:
        if not key.isascii() and key in text:
            for line in text.splitlines():
                if key not in line:
                    continue
                match = re.search(re.escape(key) + r"\s*[（(]?[^:：]*[）)]?\s*[:：]?\s*(.*)",
                                  line)
                if not match:
                    continue
                rest = match.group(1)
                token = _substring_token(rest, [c for c in enum if c != "信息不足"])
                if token:
                    evidence = rest.split("—", 1)[-1].strip() if "—" in rest else rest.strip()
                    return {"level": token, "evidence": evidence,
                            "confirm": "", "invalidate": "", "confidence": "low"}
    return None


def parse_market_regime(report: Any) -> dict:
    """把 CN Tech 报告解析为结构化 `market_regime`（dict）。

    schema（第八章 + 第十二章 State 表）：
        `short_term` / `wave` / `long_term` 各含 `level`（固定枚举）+
        `evidence` / `confirm` / `invalidate` / `confidence`；
        另含 `style` / `sentiment_cycle` / `as_of_date` / `parse_status`。

    解析顺序：JSON 对象 → 旧结论文本级 → 全量降级“信息不足”。
    解析失败/枚举缺失一律降级（不抛异常），并置 `parse_status="degraded"`，
    门控侧按“核心质量不足”处理（第十二章判据 (c)/(d)）。
    """
    regime = {
        "short_term": {"level": "信息不足", "evidence": "", "confirm": "",
                       "invalidate": "", "confidence": None},
        "wave": {"level": "信息不足", "evidence": "", "confirm": "",
                 "invalidate": "", "confidence": None},
        "long_term": {"level": "信息不足", "evidence": "", "confirm": "",
                      "invalidate": "", "confidence": None},
        "style": "信息不足",
        "sentiment_cycle": "信息不足",
        "as_of_date": None,
        "parse_status": PARSE_STATUS_DEGRADED,
        "parse_note": "",
    }
    text = report if isinstance(report, str) else ""
    data = _extract_json_object(text)

    if isinstance(data, dict):
        enums = {"short_term": SHORT_TERM_ENUM, "wave": WAVE_ENUM,
                 "long_term": LONG_TERM_ENUM}
        missing = []
        for level_key, enum in enums.items():
            entry = _norm_level_entry(data, _LEVEL_KEYS[level_key], enum)
            regime[level_key] = entry
            if entry["level"] not in enum or entry["level"] == "信息不足":
                missing.append(level_key)
        regime["style"] = _norm_enum_field(
            data, ("style", "风格", "风格方向", "market_style"), STYLE_ENUM)
        regime["sentiment_cycle"] = _norm_enum_field(
            data, ("sentiment_cycle", "情绪周期", "情绪周期位置", "sentiment"),
            SENTIMENT_CYCLE_ENUM)
        regime["as_of_date"] = normalize_date(
            data.get("as_of_date") or data.get("数据截止") or data.get("data_as_of"))
        regime["parse_status"] = PARSE_STATUS_OK if not missing else PARSE_STATUS_DEGRADED
        if missing:
            regime["parse_note"] = "级别枚举缺失/不可解析，已降级“信息不足”：%s" % (
                "、".join(missing))
        # 顶层 data_quality（若 LLM 未给出，由调用方注入）
        dq = data.get("data_quality")
        if isinstance(dq, dict):
            regime["data_quality"] = dq
        return regime

    # 旧结论文本兜底
    degraded_levels = []
    for level_key, enum in (("short_term", SHORT_TERM_ENUM), ("wave", WAVE_ENUM),
                            ("long_term", LONG_TERM_ENUM)):
        entry = _legacy_level_from_text(text, _LEVEL_KEYS[level_key], enum)
        if entry:
            regime[level_key] = entry
        else:
            degraded_levels.append(level_key)
    import re

    style_match = re.search(r"(?:市场状态标签|风格|风格方向)\s*[：:]\s*([^\n，,。;；]+)", text)
    if style_match:
        regime["style"] = _norm_enum_field({"v": style_match.group(1)}, ("v",), STYLE_ENUM)
    sent_match = re.search(r"情绪周期(?:位置)?\s*[：:]\s*([^\n，,。;；]+)", text)
    if sent_match:
        regime["sentiment_cycle"] = _norm_enum_field(
            {"v": sent_match.group(1)}, ("v",), SENTIMENT_CYCLE_ENUM)
    as_of_match = re.search(r"(?:数据截至|as_of_date)\s*[：:]\s*([0-9]{4}[-/]?[0-9]{2}[-/]?[0-9]{2})",
                            text)
    if as_of_match:
        regime["as_of_date"] = normalize_date(as_of_match.group(1))
    if degraded_levels:
        regime["parse_note"] = "结构化解析失败，级别降级“信息不足”：%s" % (
            "、".join(degraded_levels))
    else:
        regime["parse_note"] = "非 JSON 输出：按结论文本解析（置信度降级 low）"
    return regime


_CALENDAR_LEVEL_KEYS = {
    "short_term": ("short_term", "短线", "短线(5日)", "短线（5日）"),
    "wave": ("wave", "波段", "波段(20日)", "波段（20日）"),
    "long_term": ("long_term", "长线", "长线(60日)", "长线（60日）"),
}


def _norm_calendar_level(raw: Any) -> dict:
    """日历压力级别条目：`{level, score, drivers, key_dates, confidence}`。"""
    entry = {"level": "信息不足", "score": None, "drivers": [], "key_dates": [],
             "confidence": None}
    import re

    def _level_of(value: str) -> Optional[str]:
        return _substring_token(
            value, [c for c in CALENDAR_PRESSURE_ENUM if c != "信息不足"])

    if isinstance(raw, str):
        matched = _level_of(raw)
        entry["level"] = matched or "信息不足"
        if matched is None:
            entry["_level_dropped"] = True  # 评审第2轮 finding 6：未识别 ≠ 合法"信息不足"
        score_match = re.search(r"(\d)\s*分", raw) or re.search(r"压力\s*[：:]?\s*(\d)", raw)
        if score_match:
            score = int(score_match.group(1))
            if PRESSURE_SCORE_MIN <= score <= 5:
                entry["score"] = score
        dates = re.findall(r"[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}|[0-9]{1,2}月[0-9]{1,2}日", raw)
        entry["key_dates"] = dates
        entry["drivers"] = [raw.strip()] if raw.strip() else []
        return entry
    if not isinstance(raw, dict):
        return entry
    level = raw.get("level") or raw.get("压力") or raw.get("压力等级") or raw.get("结论")
    if isinstance(level, str):
        if level.strip() in CALENDAR_PRESSURE_ENUM:
            entry["level"] = level.strip()
        else:
            matched = _level_of(level)
            entry["level"] = matched or "信息不足"
            if matched is None:
                entry["_level_dropped"] = True  # 评审第2轮 finding 6
    score = raw.get("score") or raw.get("分数") or raw.get("资金压力")
    score_val = _to_float(score)
    if score_val is not None and PRESSURE_SCORE_MIN <= score_val <= 5:
        entry["score"] = int(score_val)
    entry["drivers"] = [d for d in (
        [raw.get("drivers")] if isinstance(raw.get("drivers"), str)
        else (raw.get("drivers") or raw.get("驱动项") or [])
    ) if d]
    if isinstance(raw.get("drivers"), str):
        entry["drivers"] = [raw["drivers"]]
    entry["key_dates"] = [
        normalize_date(d) or str(d) for d in
        ([raw.get("key_dates")] if isinstance(raw.get("key_dates"), str)
         else (raw.get("key_dates") or raw.get("关键日期") or []))
    ]
    entry["confidence"] = _norm_confidence(raw.get("confidence") or raw.get("置信度"))
    return entry


def parse_market_event_calendar(report: Any) -> dict:
    """把 CN News 报告解析为结构化 `market_event_calendar`（dict）。

    schema（第七章结构化输出）：`short_term` / `wave` / `long_term` 各含
    `level`（高/中/低/信息不足）、`score`（1-5）、`drivers`、`key_dates`、
    `confidence`；另含 `as_of_date` / `parse_status`。
    解析失败降级“信息不足”（门控按核心质量不足处理）。
    """
    calendar = {
        "short_term": _norm_calendar_level(None),
        "wave": _norm_calendar_level(None),
        "long_term": _norm_calendar_level(None),
        "as_of_date": None,
        "parse_status": PARSE_STATUS_DEGRADED,
        "parse_note": "",
    }
    text = report if isinstance(report, str) else ""
    data = _extract_json_object(text)
    if isinstance(data, dict):
        missing = []
        for level_key in _CALENDAR_LEVEL_KEYS:
            entry = _norm_calendar_level(_first_key(data, _CALENDAR_LEVEL_KEYS[level_key]))
            dropped = entry.pop("_level_dropped", False)
            calendar[level_key] = entry
            if entry["level"] == "信息不足" and (entry["score"] is None or dropped):
                missing.append(level_key)
        calendar["as_of_date"] = normalize_date(
            data.get("as_of_date") or data.get("数据截止"))
        calendar["parse_status"] = PARSE_STATUS_OK if not missing else PARSE_STATUS_DEGRADED
        if missing:
            calendar["parse_note"] = "级别/分数缺失或未识别，已降级“信息不足”：%s" % "、".join(missing)
        dq = data.get("data_quality")
        if isinstance(dq, dict):
            calendar["data_quality"] = dq
        return calendar

    import re

    for level_key, keys in _CALENDAR_LEVEL_KEYS.items():
        for key in keys:
            if key.isascii() or key not in text:
                continue
            line_match = re.search(
                re.escape(key) + r"\s*[（(]?[^:：]*[）)]?\s*[:：]\s*([^\n]+)", text)
            if line_match:
                calendar[level_key] = _norm_calendar_level(line_match.group(1))
                break
    as_of_match = re.search(r"(?:数据截至|as_of_date)\s*[：:]\s*([0-9]{4}[-/]?[0-9]{2}[-/]?[0-9]{2})",
                            text)
    if as_of_match:
        calendar["as_of_date"] = normalize_date(as_of_match.group(1))
    calendar["parse_note"] = "非 JSON 输出：按结论文本解析（置信度降级 low）"
    return calendar


# ---------------------------------------------------------------------------
# 全球风险评估解析（第六章字段 / 第十二章 State 表）
# ---------------------------------------------------------------------------

_SYSTEMIC_RISK_ALIASES = {
    "high": "high", "medium": "medium", "low": "low", "insufficient": "insufficient",
    "高": "high", "中": "medium", "低": "low", "信息不足": "insufficient",
}

_EVIDENCE_ROLE_ALIASES = {
    "risk_price": "risk_price", "价格证据": "risk_price", "风险价格": "risk_price",
    "price": "risk_price",
    "event_fact": "event_fact", "事件事实": "event_fact", "事件": "event_fact",
    "history_reference": "history_reference", "历史统计": "history_reference",
    "历史参考": "history_reference", "历史案例": "history_reference",
    "credit_liquidity": "credit_liquidity", "信用流动性": "credit_liquidity",
    "信用": "credit_liquidity",
}


def _norm_systemic_risk(value: Any) -> Optional[str]:
    """系统性风险 → 固定枚举；非法/缺失/否定式（「不高」「非高」）返回 `None`。

    调用方（`parse_global_risk_assessment`）对 None 降级 `insufficient` → 门控
    判据 (d) → `caution`，不得由「不高」反配为 `high` 触发 `block`（评审 M1）。
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in _SYSTEMIC_RISK_ALIASES:
        return _SYSTEMIC_RISK_ALIASES[text]
    token = _substring_token(text, _SYSTEMIC_RISK_ALIASES.keys())
    return _SYSTEMIC_RISK_ALIASES[token] if token else None


def _norm_evidence_item(raw: Any) -> Optional[dict]:
    """证据条目归一为 `{role, detail}`（角色标识见 `GLOBAL_RISK_EVIDENCE_ROLE_ENUM`）。

    字符串条目按 `role="other"`；无内容返回 `None`（不臆造证据）。
    """
    if isinstance(raw, str):
        detail = raw.strip()
        return {"role": "other", "detail": detail} if detail else None
    if not isinstance(raw, dict):
        return None
    role_raw = _norm_text(raw.get("role") or raw.get("角色") or raw.get("类型")).strip().lower()
    role = _EVIDENCE_ROLE_ALIASES.get(role_raw) or "other"
    detail = _norm_text(
        raw.get("detail") or raw.get("内容") or raw.get("证据")
        or raw.get("description") or raw.get("value"))
    if not detail:
        return None
    return {"role": role, "detail": detail}


def _norm_transmission_item(raw: Any) -> Optional[dict]:
    """传导条目归一为 `{event, channel, window, confirm, invalidate}`（行业映射必备时间窗/确认/失效）。"""
    if isinstance(raw, str):
        text = raw.strip()
        return {"event": text, "channel": "", "window": "", "confirm": "",
                "invalidate": ""} if text else None
    if not isinstance(raw, dict):
        return None
    entry = {
        "event": _norm_text(raw.get("event") or raw.get("事件") or raw.get("事件事实")),
        "channel": _norm_text(
            raw.get("channel") or raw.get("传导链") or raw.get("链条") or raw.get("path")),
        "window": _norm_text(raw.get("window") or raw.get("时间窗") or raw.get("窗口")),
        "confirm": _norm_text(raw.get("confirm") or raw.get("确认条件")),
        "invalidate": _norm_text(raw.get("invalidate") or raw.get("失效条件")),
    }
    return entry if any(entry.values()) else None


def _coverage_insufficient(coverage: Any) -> bool:
    """核心风险价格缺失过半（第六章判定约束）？"""
    if not isinstance(coverage, dict):
        return False
    if coverage.get("status") == "insufficient":
        return True
    ratio = _to_float(coverage.get("ratio"))
    return ratio is not None and ratio < GLOBAL_RISK_CORE_MIN_RATIO


def parse_global_risk_assessment(report: Any, coverage: Any = None) -> dict:
    """把国际影响报告解析为结构化 `global_risk_assessment`（dict）。

    schema（第六章 + 第十二章 State 表）：`as_of_date`、`risk_appetite`
    （进攻/中性/避险/信息不足）、`systemic_risk`（high/medium/low/insufficient）、
    `confidence`（high/medium/low）、带角色标识的 `evidence`、`event_transmissions`、
    `parse_status` / `parse_note`（`data_quality` 子字段由调用方注入）。

    `coverage`（`build_global_risk_features` 的 `derived_metrics.coverage`）显示
    核心风险价格缺失过半时，按第六章判定约束**代码级强制** `risk_appetite` 与
    `systemic_risk` 为 `insufficient`（不依赖 LLM 遵守提示词）。

    解析顺序：JSON 对象 → 文本行兜底 → 全量降级（`systemic_risk=insufficient`）。
    解析失败/必需枚举缺失置 `parse_status="degraded"`（门控按核心质量不足处理）。
    """
    assessment = {
        "risk_appetite": "信息不足",
        "systemic_risk": "insufficient",
        "confidence": None,
        "evidence": [],
        "event_transmissions": [],
        "as_of_date": None,
        "parse_status": PARSE_STATUS_DEGRADED,
        "parse_note": "",
    }
    text = report if isinstance(report, str) else ""
    data = _extract_json_object(text)

    if isinstance(data, dict):
        missing = []
        appetite = _norm_enum_field(
            data, ("risk_appetite", "风险偏好", "全球风险偏好"), RISK_APPETITE_ENUM)
        if appetite == "信息不足":
            missing.append("risk_appetite")
        assessment["risk_appetite"] = appetite

        systemic = _norm_systemic_risk(
            _first_key(data, ("systemic_risk", "系统性风险", "系统风险")))
        if systemic is None:
            missing.append("systemic_risk")
            systemic = "insufficient"
        assessment["systemic_risk"] = systemic
        assessment["confidence"] = _norm_confidence(
            _first_key(data, ("confidence", "置信度")))

        raw_evidence = _first_key(data, ("evidence", "证据")) or []
        if not isinstance(raw_evidence, list):
            raw_evidence = [raw_evidence]
        assessment["evidence"] = [
            item for item in (_norm_evidence_item(e) for e in raw_evidence) if item]

        raw_transmissions = _first_key(
            data, ("event_transmissions", "传导", "事件传导", "transmissions")) or []
        if not isinstance(raw_transmissions, list):
            raw_transmissions = [raw_transmissions]
        assessment["event_transmissions"] = [
            item for item in (_norm_transmission_item(t) for t in raw_transmissions) if item]

        assessment["as_of_date"] = normalize_date(
            data.get("as_of_date") or data.get("数据截止") or data.get("data_as_of"))
        assessment["parse_status"] = PARSE_STATUS_OK if not missing else PARSE_STATUS_DEGRADED
        if missing:
            assessment["parse_note"] = "必需枚举缺失/不可解析，已降级：%s" % "、".join(missing)
        dq = data.get("data_quality")
        if isinstance(dq, dict):
            assessment["data_quality"] = dq
    else:
        # 文本行兜底（非 JSON 输出）
        import re

        appetite_match = re.search(
            r"(?:全球风险偏好|风险偏好|risk_appetite)\s*[：:]\s*([^\n，,。;；]+)", text)
        if appetite_match:
            assessment["risk_appetite"] = _norm_enum_field(
                {"v": appetite_match.group(1)}, ("v",), RISK_APPETITE_ENUM)
        systemic_match = re.search(
            r"(?:systemic_risk|系统性风险|系统风险)\s*[：:]\s*([^\n，,。;；]+)", text)
        if systemic_match:
            systemic = _norm_systemic_risk(systemic_match.group(1))
            if systemic is not None:
                assessment["systemic_risk"] = systemic
        confidence_match = re.search(
            r"(?:confidence|置信度)\s*[：:]\s*([^\n，,。;；]+)", text)
        if confidence_match:
            assessment["confidence"] = _norm_confidence(confidence_match.group(1))
        as_of_match = re.search(
            r"(?:数据截至|as_of_date)\s*[：:]\s*([0-9]{4}[-/]?[0-9]{2}[-/]?[0-9]{2})", text)
        if as_of_match:
            assessment["as_of_date"] = normalize_date(as_of_match.group(1))
        assessment["parse_note"] = "非 JSON 输出：按文本行解析（禁止据此补写风险价格证据）"

    if _coverage_insufficient(coverage):
        assessment["risk_appetite"] = "信息不足"
        assessment["systemic_risk"] = "insufficient"
        note = ("核心风险价格缺失过半（coverage.status=insufficient），"
                "风险偏好与系统风险按第六章判定约束强制 insufficient")
        assessment["parse_note"] = (assessment["parse_note"] + "；" + note) if assessment[
            "parse_note"] else note
    return assessment


# ===========================================================================
# 证据文本（Markdown，供 Prompt 注入；不可用返回非 "#" 开头的降级说明）
# ===========================================================================

def _fmt(value: Any, digits: int = 2, suffix: str = "", empty: str = "信息不足") -> str:
    if value is None:
        return empty
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _fmt_metric(metric: Any, digits: int = 2, suffix: str = "") -> str:
    if not isinstance(metric, dict) or metric.get("value") is None:
        return "信息不足"
    return _fmt(metric.get("value"), digits, suffix)


def _series_line(label: str, entry: dict, windows: Iterable[int],
                 digits: int = 2, suffix: str = "") -> str:
    """`最新 x（5日均 y / 20日均 z；5日变化 d）` 形式的一行摘要。"""
    if not isinstance(entry, dict) or entry.get("latest") is None:
        return f"- {label}：信息不足"
    parts = [f"最新 {_fmt(entry.get('latest'), digits, suffix)}"]
    ma_bits = []
    for w in windows:
        ma = _metric_value(entry.get("ma") or {}, w)
        if ma is not None:
            ma_bits.append(f"{w}日均 {_fmt(ma, digits, suffix)}")
    change_bits = []
    for w in windows:
        metric = (entry.get("change") or {}).get(w) or {}
        pct = (metric.get("pct") or {}).get("value")
        if pct is not None:
            change_bits.append(f"{w}日变化 {_fmt(pct, digits, '%')}")
    if ma_bits:
        parts.append("；".join(ma_bits))
    if change_bits:
        parts.append("；".join(change_bits))
    tail = ("（" + "；".join(parts[1:]) + "）") if len(parts) > 1 else ""
    return f"- {label}：" + parts[0] + tail


def format_cn_technical_evidence(features: dict) -> str:
    """CN Tech 特征 → Prompt 证据块（可用时以 `# ` 开头，不可用时降级说明）。"""
    if not isinstance(features, dict) or not features.get("derived_metrics"):
        return "市场技术特征不可用：请将该级别输出降级为“信息不足”（不阻断全图）。"
    derived = features.get("derived_metrics") or {}
    lbs = features.get("lookbacks") or list(DEFAULT_TECH_LOOKBACKS)
    lines = [f"# 市场技术特征（数据截至 {features.get('as_of_date') or '信息不足'}，"
             f"分析日 {features.get('analysis_date')}）"]
    lines.append("窗口均为交易日口径；“信息不足”表示样本不足（N 日窗口需 N 个样本）。")

    lines.append("## 指数趋势与收益")
    for code, entry in (derived.get("indices") or {}).items():
        ma_bits = " / ".join(
            f"MA{w} {_fmt_metric((entry.get('ma') or {}).get(w))}" for w in lbs)
        ret_bits = " / ".join(
            f"{w}日 {_fmt_metric((entry.get('return') or {}).get(w), 2, '%')}" for w in lbs)
        slope = (entry.get("slope") or {}).get(20) or (entry.get("slope") or {}).get(max(lbs))
        lines.append(
            f"- {entry.get('name')}({code}) 截至 {entry.get('as_of_date')}，样本 {entry.get('samples')}："
            f"收盘 {_fmt_metric(entry.get('close'))}；{ma_bits}；收益 {ret_bits}；"
            f"斜率 {_fmt_metric(slope, 3, '%/日')}")
    if not (derived.get("indices") or {}):
        lines.append("- 指数序列不可用：宽基趋势/风格证据缺失")

    style = derived.get("style") or {}
    lines.append("## 风格（%d 日相对收益差，pp）" % SWING_LOOKBACK)
    for key, entry in (style.get("pairs") or {}).items():
        if entry.get("sufficient"):
            lines.append(f"- {entry.get('label')}：{_fmt(entry.get('value'), 2)}（>0 表示前者强）")
        else:
            lines.append(f"- {entry.get('label')}：信息不足（{entry.get('note') or '样本不足'}）")
    lines.append(f"- 风格标签：{style.get('label')}")

    turnover = derived.get("turnover") or {}
    lines.append("## 成交额（上证+深证成指合计）")
    if turnover:
        series = turnover.get("series") or {}
        lines.append(
            f"- 截至 {turnover.get('as_of_date')}，样本 {turnover.get('samples')}；"
            + _series_line("成交额", {
                "latest": series.get("latest"),
                "ma": series.get("ma") or {},
                "change": series.get("change") or {},
            }, (5, 20), 0, " 千元")[2:])
    else:
        lines.append("- 成交额不可用（短线“成交额”判据缺失）")

    breadth = derived.get("breadth") or {}
    lines.append("## 市场宽度与高标情绪")
    if breadth.get("samples"):
        lines.append(f"- 截至 {breadth.get('as_of_date')}，样本 {breadth.get('samples')} 个交易日")
        for key, label, digits, suffix in (
            ("up_ratio", "上涨占比", 3, ""),
            ("limit_up", "涨停家数", 0, ""),
            ("limit_down", "跌停家数", 0, ""),
            ("broken_board_rate", "炸板率", 3, ""),
            ("max_board", "最高连板", 0, ""),
            ("promotion_rate", "晋级率", 3, ""),
            ("premium_rate", "昨日涨停溢价", 4, ""),
        ):
            lines.append(_series_line(label, (breadth.get("series") or {}).get(key) or {},
                                      (5, 20), digits, suffix))
        if breadth.get("missing"):
            lines.append("- 缺失：" + "、".join(breadth.get("missing")))
    else:
        lines.append("- 宽度/高标情绪序列不可用")

    fund = derived.get("fund_flow") or {}
    lines.append("## 资金（主力/北向，净额单位：万元；窗口内累加）")
    if fund.get("samples"):
        lines.append(f"- 截至 {fund.get('as_of_date')}，样本 {fund.get('samples')}")
        for key, label in (("main_net", "主力净流入"), ("northbound", "北向净流入")):
            entry = fund.get(key) or {}
            if entry.get("latest") is None:
                lines.append(f"- {label}：信息不足")
                continue
            sums = "；".join(
                f"{w}日累计 {_fmt_metric((entry.get('sum') or {}).get(w), 0)}"
                for w in (5, 20) if (entry.get("sum") or {}).get(w, {}).get("value") is not None)
            lines.append(f"- {label}：最新 {_fmt(entry.get('latest'), 0)}" +
                         (f"（{sums}）" if sums else ""))
    else:
        lines.append("- 资金序列不可用")

    margin = derived.get("margin") or {}
    lines.append("## 两融（融资余额）")
    if margin.get("samples"):
        rzye = margin.get("rzye") or {}
        change_bits = []
        for w in (1, 5, 20):
            metric = (rzye.get("change") or {}).get(w) or {}
            pct = (metric.get("pct") or {}).get("value")
            if pct is not None:
                change_bits.append(f"{w}日 {_fmt(pct, 2, '%')}")
        lines.append(
            f"- 截至 {margin.get('as_of_date')}：余额 {_fmt((rzye.get('latest') or 0) / 1e8 if rzye.get('latest') else None, 0, ' 亿元')}；"
            + ("变化 " + "；".join(change_bits) if change_bits else "变化 信息不足"))
    else:
        lines.append("- 两融序列不可用（资金趋势证据缺失）")

    valuation = derived.get("valuation") or {}
    lines.append("## 估值（长线）")
    if valuation.get("index"):
        for code, entry in valuation["index"].items():
            lines.append(
                f"- {entry.get('name')}({code}) 截至 {entry.get('last_date')}："
                f"PE_TTM {_fmt(entry.get('pe_ttm_latest'))}（历史分位 {_fmt(entry.get('pe_ttm_pct'))}%）、"
                f"PB {_fmt(entry.get('pb_latest'))}（分位 {_fmt(entry.get('pb_pct'))}%）、"
                f"样本 {entry.get('samples')}")
    else:
        lines.append("- 指数估值分位不可用")
    all_a = valuation.get("all_a") or {}
    if all_a:
        lines.append(
            f"- 全 A 快照（{all_a.get('trade_date')}，{all_a.get('rows')} 只）："
            f"PE_TTM 中位数 {_fmt(all_a.get('pe_ttm_median'))}、PB 中位数 {_fmt(all_a.get('pb_median'))}；"
            f"{all_a.get('note') or ''}")
    lines.append(f"- 估值级别：{valuation.get('level')}")
    if valuation.get("missing"):
        lines.append("- 缺失：" + "；".join(valuation["missing"]))

    liquidity = derived.get("liquidity") or {}
    lines.append("## 流动性（长线）")
    shibor = (liquidity.get("shibor") or {})
    if shibor:
        lines.append(_series_line("Shibor 1W", shibor.get("w1") or {}, (5, 20), 4, "%"))
        lines.append(_series_line("Shibor 隔夜", shibor.get("on") or {}, (5,), 4, "%"))
    ms = (liquidity.get("money_supply") or {})
    if ms:
        lines.append(_series_line("M2 同比", ms.get("m2_yoy") or {}, (1, 3), 2, "%"))
        lines.append(_series_line("M1 同比", ms.get("m1_yoy") or {}, (1, 3), 2, "%"))
    lpr = (liquidity.get("lpr") or {})
    if lpr:
        lines.append(_series_line("LPR 1Y", lpr.get("y1") or {}, (1, 5), 3, "%"))
    lines.append(f"- 流动性方向：{liquidity.get('direction')}")
    if liquidity.get("missing"):
        lines.append("- 缺失（已降级标注）：" + "；".join(liquidity["missing"]))

    lines.append("## 判据覆盖（第八章：每级至少两类证据）")
    for level, entry in (derived.get("evidence_coverage") or {}).items():
        lines.append(
            f"- {level}：可用 {'/'.join(entry.get('available') or []) or '无'}；"
            f"缺失 {'/'.join(entry.get('missing') or []) or '无'}；"
            f"{'满足最低两类' if entry.get('satisfied') else '不足两类 → 应输出“信息不足”'}")
    if features.get("missing_inputs"):
        lines.append("## 输入缺失清单")
        lines.append("- " + "；".join(features.get("missing_inputs")))
    # 数据源标注（评审残留）：与 `format_global_risk_evidence` 同模式——未来行
    # 丢弃事实、as_of 不可解析（防前视未生效）等必须随证据块进入 Prompt
    if features.get("notes"):
        lines.append("## 数据源标注")
        for note in features["notes"]:
            lines.append(f"- {note}")
    return "\n".join(lines)


def format_cn_event_calendar_evidence(features: dict) -> str:
    """CN News 特征 → Prompt 证据块（压力分级 + 驱动 + 关键日期 + 缺失）。"""
    if not isinstance(features, dict) or not features.get("derived_metrics"):
        return "资金日历特征不可用：请输出“信息不足”，不得推断资金压力。"
    derived = features.get("derived_metrics") or {}
    raw = features.get("raw_reference") or {}
    lines = [f"# 资金日历特征（数据截至 {features.get('as_of_date') or '信息不足'}，"
             f"分析日 {features.get('analysis_date')}；窗口为交易日口径）"]
    for window, entry in (derived.get("windows_pressure") or {}).items():
        lines.append(f"## 未来 {window} 个交易日")
        lines.append(f"- 压力分级：{entry.get('level')}；分数 {_fmt(entry.get('score'), 0)}"
                     f"（1-5）；置信度 {entry.get('confidence') or 'low'}"
                     f"；窗口截止 {entry.get('window_end')}")
        for driver in entry.get("drivers") or []:
            lines.append(f"  - [{driver.get('source')}] {driver.get('detail')}")
        for kd in entry.get("key_dates") or []:
            lines.append(f"  - 关键日期 {kd.get('date')}（{kd.get('source')}）：{kd.get('detail')}")
        if entry.get("missing"):
            lines.append("  - 缺失/不可评估：" + "；".join(entry.get("missing")))
    margin = derived.get("margin") or {}
    lines.append("## 两融")
    if margin.get("as_of_date"):
        lines.append(_series_line("融资余额", {"latest": margin.get("rzye_latest"),
                                              "ma": {},
                                              "change": {}}, (1,), 0, " 元") +
                     f"；截至 {margin.get('as_of_date')}（{margin.get('note')}）")
    else:
        lines.append("- 两融不可用：资金压力维度缺失（不评估）")
    if raw.get("holiday_windows"):
        lines.append("## 长假窗口")
        for h in raw["holiday_windows"]:
            lines.append(f"- {h.get('start')} ~ {h.get('end')}（{h.get('days')} 天）")
    if features.get("missing_inputs"):
        lines.append("## 缺失清单")
        lines.append("- " + "；".join(features.get("missing_inputs")))
    lines.append("## 说明")
    lines.append("- 解禁为日历事实，不得写成必然卖压；仅募资额/发行规模可判断 IPO 抽血强度。")
    return "\n".join(lines)


def format_global_risk_evidence(features: dict) -> str:
    """全球风险特征 → Prompt 证据块（美债/美股/波动率/汇率/商品 + 覆盖度）。"""
    if not isinstance(features, dict) or not features.get("derived_metrics"):
        return "全球风险价格特征不可用：请将 systemic_risk 输出为 insufficient。"
    derived = features.get("derived_metrics") or {}
    lbs = features.get("lookbacks") or list(DEFAULT_GLOBAL_LOOKBACKS)
    lines = [f"# 全球风险价格特征（数据截至 {features.get('as_of_date') or '信息不足'}，"
             f"分析日 {features.get('analysis_date')}）"]
    lines.append("## 美债（%）")
    for key, label in (("y10", "美债 10Y"), ("y2", "美债 2Y")):
        entry = (derived.get("us_rates") or {}).get(key)
        if entry:
            lines.append(_series_line(label, entry, lbs, 3, "%"))
    spread = derived.get("term_spread")
    if spread:
        lines.append(f"- 期限利差（10Y-2Y，pp）：{_fmt(spread.get('latest'), 3)}"
                     f"（{spread.get('note')}）")
    lines.append("## 美股与波动率")
    for code, entry in (derived.get("equities") or {}).items():
        rets = "；".join(f"{w}日 {_fmt_metric((entry.get('return') or {}).get(w), 2, '%')}"
                         for w in lbs)
        lines.append(f"- {code}：最新 {_fmt(entry.get('latest'), 2)}；收益 {rets}")
    vol = derived.get("volatility") or {}
    if vol:
        lines.append(f"- 波动率：{_fmt(vol.get('value'), 2)}%（{vol.get('method')}，"
                     f"替代 VIX；{vol.get('note')}）")
    else:
        lines.append("- 波动率：信息不足（无 VIX 代码，且美股样本不足）")
    lines.append("## 汇率与商品")
    for code, entry in (derived.get("fx") or {}).items():
        rets = "；".join(f"{w}日 {_fmt_metric((entry.get('return') or {}).get(w), 2, '%')}"
                         for w in lbs)
        lines.append(f"- {code}：最新 {_fmt(entry.get('latest'), 4)}；收益 {rets}")
    for code, entry in (derived.get("commodities") or {}).items():
        rets = "；".join(f"{w}日 {_fmt_metric((entry.get('return') or {}).get(w), 2, '%')}"
                         for w in lbs)
        lines.append(f"- {code}（国内价、非 WTI/伦金）：最新 {_fmt(entry.get('latest'), 2)}；"
                     f"收益 {rets}")
    coverage = derived.get("coverage") or {}
    lines.append("## 核心覆盖度（第六章阶段 3 特征组）")
    lines.append(f"- 可用：{'/'.join(coverage.get('available') or []) or '无'}；"
                 f"缺失：{'/'.join(coverage.get('missing') or []) or '无'}；"
                 f"比例 {_fmt(coverage.get('ratio'), 2)}；级别 {coverage.get('status')}")
    if features.get("notes"):
        lines.append("## 数据源标注")
        for note in features["notes"]:
            lines.append(f"- {note}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# 下游消费：结构化字段 → 紧凑 Markdown 摘要（第十一章下游上下文优先级）
# ---------------------------------------------------------------------------

def _quality_line(node_output: Any) -> str:
    """节点级 data_quality 子字段 → 一行摘要（无可标注返回空串）。"""
    if not isinstance(node_output, dict):
        return ""
    dq = node_output.get("data_quality")
    if not isinstance(dq, dict):
        return ""
    bits = [f"降级级别 {dq.get('degradation_level') or '未标注'}"]
    if dq.get("as_of_date"):
        bits.append(f"数据截至 {dq['as_of_date']}")
    missing = dq.get("missing_inputs")
    if missing:
        bits.append("缺失 " + "、".join(str(m) for m in list(missing)[:5]))
    if dq.get("parse_status"):
        bits.append(f"解析 {dq['parse_status']}")
    return "- 数据质量：" + "；".join(bits)


def format_global_risk_assessment_summary(assessment: Any, max_evidence: int = 5) -> str:
    """`global_risk_assessment` → 紧凑 Markdown 摘要（下游跨层上下文首选输入）。

    空/非 dict 返回空串（调用方按“无该字段”处理）。
    """
    if not isinstance(assessment, dict) or not any(
            assessment.get(k) for k in
            ("systemic_risk", "risk_appetite", "evidence", "event_transmissions")):
        return ""
    lines = ["## 全球风险评估（市场层）"]
    lines.append(
        f"- 风险偏好：{assessment.get('risk_appetite') or '信息不足'}"
        f"；系统性风险：{assessment.get('systemic_risk') or 'insufficient'}"
        f"；置信度：{assessment.get('confidence') or '未给出'}"
        + (f"；数据截至 {assessment['as_of_date']}" if assessment.get("as_of_date") else ""))
    evidence = [e for e in (assessment.get("evidence") or []) if isinstance(e, dict)]
    if evidence:
        lines.append("- 证据（角色标识）：")
        for item in evidence[:max_evidence]:
            lines.append(f"  - [{item.get('role') or 'other'}] {item.get('detail')}")
    transmissions = [t for t in (assessment.get("event_transmissions") or [])
                     if isinstance(t, dict)]
    if transmissions:
        lines.append("- 事件传导（含时间窗/确认/失效条件）：")
        for item in transmissions[:max_evidence]:
            tail = []
            if item.get("window"):
                tail.append(f"窗口 {item['window']}")
            if item.get("confirm"):
                tail.append(f"确认 {item['confirm']}")
            if item.get("invalidate"):
                tail.append(f"失效 {item['invalidate']}")
            lines.append(
                f"  - {item.get('event') or '（事件未标注）'}"
                f"｜传导 {item.get('channel') or '未标注'}"
                + ("｜" + "；".join(tail) if tail else ""))
    quality = _quality_line(assessment)
    if quality:
        lines.append(quality)
    return "\n".join(lines)


def format_events_summary(events: Any, max_events: int = 5) -> str:
    """`international_events`（或同构事件列表）→ 紧凑 Markdown 摘要。

    历史统计只渲染预取字段（`historical_impact`），无统计时标注原因
    （不得由消费方补写数值）。
    """
    if not isinstance(events, list) or not events:
        return ""
    lines = ["## 结构化事件（历史参考，含局限）"]
    for item in events[:max_events]:
        if not isinstance(item, dict):
            continue
        refs = "、".join(str(r) for r in (item.get("affected_scope_refs") or [])) or "无"
        lines.append(
            f"- 《{item.get('fact') or item.get('title') or '（无标题）'}》"
            f"（{item.get('event_time') or '时间未知'}；来源 {item.get('source') or '未知'}"
            f"；作用域 {item.get('event_scope') or '-'}，目标 {refs}）")
        impact = item.get("historical_impact")
        status = item.get("history_match_status") or "unmatched"
        if isinstance(impact, dict):
            lines.append(
                f"  - 历史统计（预取）：样本 {impact.get('sample_count')}；"
                f"加权 CAR {_fmt(impact.get('weighted_car'), 4) if impact.get('weighted_car') is not None else '无'}；"
                f"胜率 {_fmt((impact.get('win_rate') or 0) * 100 if impact.get('win_rate') is not None else None, 1, '%')}；"
                f"污染样本 {impact.get('contaminated_sample_count')}；"
                f"匹配状态 {status}")
        else:
            lines.append(f"  - 历史统计：无（匹配状态 {status}）")
        notes = (item.get("data_quality") or {}).get("notes") or []
        if notes:
            lines.append("  - 局限：" + "；".join(str(n) for n in notes[:3]))
    lines.append("> 历史统计仅为条件化参考，不得表述为当期已发生收益；无统计时由消费方保持“无历史验证”。")
    return "\n".join(lines)


def format_market_regime_summary(regime: Any) -> str:
    """`market_regime`（dict）→ 紧凑 Markdown 摘要（替代原文本直接拼接）。"""
    if not isinstance(regime, dict) or not regime:
        return ""
    labels = (("short_term", "短线(5日)"), ("wave", "波段(20日)"),
              ("long_term", "长线(60日)"))
    lines = ["## 大盘环境判定（市场层）"]
    for key, label in labels:
        entry = regime.get(key)
        if not isinstance(entry, dict):
            continue
        bits = [f"{label}：{entry.get('level') or '信息不足'}"]
        if entry.get("confidence"):
            bits.append(f"置信度 {entry['confidence']}")
        if entry.get("evidence"):
            bits.append(f"证据 {entry['evidence']}")
        if entry.get("confirm"):
            bits.append(f"确认 {entry['confirm']}")
        if entry.get("invalidate"):
            bits.append(f"失效 {entry['invalidate']}")
        lines.append("- " + "；".join(bits))
    extras = [f"风格 {regime.get('style')}"] if regime.get("style") else []
    if regime.get("sentiment_cycle"):
        extras.append(f"情绪周期 {regime['sentiment_cycle']}")
    if extras:
        lines.append("- " + "；".join(extras))
    quality = _quality_line(regime)
    if quality:
        lines.append(quality)
    return "\n".join(lines) if len(lines) > 1 else ""


def format_market_event_calendar_summary(calendar: Any) -> str:
    """`market_event_calendar`（dict）→ 紧凑 Markdown 摘要。"""
    if not isinstance(calendar, dict) or not calendar:
        return ""
    labels = (("short_term", "短线(5日)"), ("wave", "波段(20日)"),
              ("long_term", "长线(60日)"))
    lines = ["## 资金日历（市场层）"]
    for key, label in labels:
        entry = calendar.get(key)
        if not isinstance(entry, dict):
            continue
        bits = [f"{label}：风险 {entry.get('level') or '信息不足'}"]
        if entry.get("score") is not None:
            bits.append(f"资金压力 {entry['score']}/5")
        if entry.get("drivers"):
            drivers = entry["drivers"]
            drivers = drivers if isinstance(drivers, list) else [drivers]
            bits.append("驱动 " + "；".join(str(d) for d in drivers[:3]))
        if entry.get("key_dates"):
            bits.append("关键日期 " + "、".join(str(d) for d in entry["key_dates"][:5]))
        lines.append("- " + "；".join(bits))
    quality = _quality_line(calendar)
    if quality:
        lines.append(quality)
    return "\n".join(lines) if len(lines) > 1 else ""


__all__ = [
    "DEFAULT_TECH_LOOKBACKS",
    "DEFAULT_CALENDAR_WINDOWS",
    "DEFAULT_GLOBAL_LOOKBACKS",
    "build_data_quality_summary",
    "probe_dataset_availability",
    "node_data_quality",
    "build_cn_technical_features",
    "build_cn_event_calendar_features",
    "build_global_risk_features",
    "derive_risk_gate",
    "parse_market_regime",
    "parse_market_event_calendar",
    "parse_global_risk_assessment",
    "format_cn_technical_evidence",
    "format_cn_event_calendar_evidence",
    "format_global_risk_evidence",
    "format_global_risk_assessment_summary",
    "format_events_summary",
    "format_market_regime_summary",
    "format_market_event_calendar_summary",
]
