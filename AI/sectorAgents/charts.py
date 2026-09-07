"""
板块层热力图生成模块（板块层轮动战术与政策事件流方案 3.2）

生成行业 + 概念近 10 日逐日涨跌幅热力图（plotly 自包含 HTML），写入 run 级
日志目录 logs/{ts}/reports/charts/sector_daily_heatmaps.html，由 logviewer
报告 tab 的 .html 渲染分支展示。

- 单侧数据不可用时不拖累另一侧（任一可用即写文件）
- 两侧均不可用 → 返回 None（无图不报错）
- plotly 在函数内 import（未安装时异常被调用点兜住，不阻塞分析）
- 图表设计遵循项目 dataviz skill 约定：diverging 双色臂（A 股惯例红涨绿跌）
  + 中性灰中点、zmid=0、悬停交互、色标图例；文字标注口径不靠颜色单独表意
"""

import logging
from pathlib import Path

from AI.dataflows import interface as dataflow
from AI.dataflows.providers.cn import daily_matrix_utils

logger = logging.getLogger(__name__)

# diverging colorscale：负=绿（跌）、中点=中性灰、正=红（涨），zmid=0 对齐
_DIVERGING_COLORSCALE = [
    [0.0, "#006300"],   # 深绿（深跌）
    [0.25, "#0ca30c"],  # 绿
    [0.5, "#f0efec"],   # 中性灰（≈0）
    [0.75, "#e34948"],  # 红
    [1.0, "#d03b3b"],   # 深红（大涨）
]


def _is_valid_matrix(data) -> bool:
    """结构化矩阵可用性判定：非空 + 日期 ≥2 + 行数与名称一致 + 每行长度与日期轴一致。"""
    if not data:
        return False
    dates = data.get("dates") or []
    names = data.get("names") or []
    matrix = data.get("pct_matrix") or []
    if len(dates) < 2 or not names or len(matrix) != len(names):
        return False
    return all(len(row) == len(dates) for row in matrix)


def _sort_rows_by_cum(data: dict):
    """按复利累计涨幅降序重排行序（口径与 daily_matrix_utils.cum_pct_from_daily 一致）。

    返回 (names, matrix)。累计为 None（全缺日）排最末。
    """
    def cum_of(row):
        c = daily_matrix_utils.cum_pct_from_daily(
            {d: v for d, v in zip(data["dates"], row) if v is not None})
        return c if c is not None else -1e9

    rows = list(zip(data["names"], data["pct_matrix"]))
    rows.sort(key=lambda nr: cum_of(nr[1]), reverse=True)
    return [n for n, _ in rows], [m for _, m in rows]


def _build_heatmap_trace(data: dict, kind: str):
    """构建单个热力图 trace（纯函数，单测可构造）。

    kind: "industry" / "concept"。x=日期（MM-DD），y=板块名，z=逐日涨跌幅%。
    zmin/zmax 对称取 |z| 最大值（zmid=0 精确落在中性灰）。
    """
    import plotly.graph_objects as go

    names, matrix = _sort_rows_by_cum(data)
    max_abs = 0.1
    for row in matrix:
        for v in row:
            if v is not None:
                max_abs = max(max_abs, abs(float(v)))
    return go.Heatmap(
        x=[daily_matrix_utils.fmt_date_short(d) for d in data["dates"]],
        y=names,
        z=matrix,
        zmin=-max_abs,
        zmax=max_abs,
        zmid=0,
        colorscale=_DIVERGING_COLORSCALE,
        hovertemplate="%{y} · %{x} · %{z:+.2f}%<extra></extra>",
        colorbar={"title": "涨跌幅%"},
        name=kind,
    )


def _subtitle(data: dict, kind: str) -> str:
    """口径标注：行业侧 tushare=申万一级、akshare=东财行业；概念侧均为东财概念热度 TOP N。

    概念 N 取实际行数（len(names) ≤ 请求的 top_n）：行集合即热度 TOP N 章节的
    实际展示集，标注与实际数据一致优于标注请求参数。
    """
    src = data.get("source", "")
    if kind == "industry":
        label = "申万一级行业" if src == "tushare" else "东财行业板块"
    else:
        label = f"东财概念板块（热度 TOP {len(data['names'])}）"
    d0, d1 = data["dates"][0], data["dates"][-1]
    return f"{label} 近{len(data['dates'])}日逐日涨跌幅（{daily_matrix_utils.fmt_date_short(d0)} ~ {daily_matrix_utils.fmt_date_short(d1)}）"


def generate_sector_heatmaps(log_dir: Path | None) -> Path | None:
    """生成行业 + 概念双热力图 HTML → logs/{ts}/reports/charts/sector_daily_heatmaps.html。

    log_dir 为 None（日志未初始化）→ 返回 None；
    单侧数据不可用 → 仅画另一侧；两侧均不可用/生成失败 → 返回 None（不抛异常）。
    """
    if log_dir is None:
        return None
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        industry = dataflow.get_industry_daily_returns_matrix(days=10)
        concept = dataflow.get_concept_daily_returns_matrix(days=10, top_n=30)

        blocks = []
        for kind, matrix in (("industry", industry), ("concept", concept)):
            if _is_valid_matrix(matrix):
                blocks.append((_build_heatmap_trace(matrix, kind),
                               _subtitle(matrix, kind)))
        if not blocks:
            logger.info("[板块轮动预测] 热力图数据均不可用，跳过生成")
            return None

        if len(blocks) == 2:
            fig = make_subplots(rows=2, cols=1,
                                subplot_titles=[b[1] for b in blocks],
                                vertical_spacing=0.10)
            fig.add_trace(blocks[0][0], row=1, col=1)
            fig.add_trace(blocks[1][0], row=2, col=1)
        else:
            fig = go.Figure(blocks[0][0])
            fig.update_layout(title=blocks[0][1])

        fig.update_layout(
            paper_bgcolor="#fcfcfb",
            plot_bgcolor="#fcfcfb",
            height=380 * len(blocks),
            margin=dict(l=110, r=50, t=60, b=30),
        )

        out_dir = log_dir / "reports" / "charts"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "sector_daily_heatmaps.html"
        fig.write_html(path, include_plotlyjs="inline")
        logger.info("[板块轮动预测] 热力图已生成: %s", path)
        return path
    except Exception as e:
        logger.warning(f"[板块轮动预测] 热力图生成失败（不阻塞分析）: {e}")
        return None
