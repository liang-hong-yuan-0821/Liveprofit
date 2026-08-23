"""
板块层热力图模块单测（方案 3.2.3）

- _is_valid_matrix：None/日期不足/行数行宽不匹配判定
- _build_heatmap_trace：行按复利累计涨幅降序、zmin/zmax 对称（zmid=0）、
  colorscale 红涨绿跌、hovertemplate 含名称、x 轴 MM-DD、None 格保留
- generate_sector_heatmaps：双侧可用写双图文件 / 仅一侧可用写单图 /
  双侧均不可用返回 None 不写文件 / 空矩阵与 len(dates)<2 跳过 / log_dir=None 返回 None
"""

from unittest.mock import MagicMock

import pytest

from AI.sectorAgents import charts


def _synthetic(dates=None, names=None, matrix=None, source="tushare"):
    """空列表是合法测试输入（跳过分支），不能用 `or` 兜底"""
    return {
        "source": source,
        "dates": dates if dates is not None else ["20260818", "20260819"],
        "names": names if names is not None else ["银行", "电子"],
        "pct_matrix": matrix if matrix is not None else [[1.0, 2.0], [-1.0, 0.5]],
    }


# ==================== 矩阵可用性 ====================

def test_is_valid_matrix_cases():
    assert charts._is_valid_matrix(_synthetic())
    assert not charts._is_valid_matrix(None)
    assert not charts._is_valid_matrix(_synthetic(dates=["20260819"]))   # 日期 < 2
    assert not charts._is_valid_matrix(_synthetic(dates=[], names=[], matrix=[]))
    assert not charts._is_valid_matrix(_synthetic(matrix=[[1.0, 2.0]]))  # 行数与 names 不符
    assert not charts._is_valid_matrix(_synthetic(matrix=[[1.0], [-1.0]]))  # 行长与日期轴不符


# ==================== trace 构建 ====================

def test_build_heatmap_trace_sorts_by_cum_desc():
    """行按复利累计涨幅降序（口径同 cum_pct_from_daily）"""
    data = _synthetic(names=["A", "B", "C"],
                      matrix=[[0.0, 0.0], [5.0, 5.0], [-3.0, -3.0]])
    trace = charts._build_heatmap_trace(data, "industry")

    assert list(trace.y) == ["B", "A", "C"]     # B 复利 +10.25% > A 0% > C 负
    assert trace.zmid == 0
    assert trace.zmin == -trace.zmax            # 对称色标，0 落在中性灰
    assert trace.zmin == -5.0
    # plotly 会把 colorscale 列表转成 tuple 的 tuple，逐项比对即可
    assert tuple(map(tuple, trace.colorscale)) == tuple(map(tuple, charts._DIVERGING_COLORSCALE))
    assert trace.colorbar["title"].text == "涨跌幅%"
    assert "%{y}" in trace.hovertemplate and "%{z:+.2f}%" in trace.hovertemplate
    assert list(trace.x)[0] == "08-18"          # 列头 MM-DD


def test_build_heatmap_trace_none_cell_kept():
    data = _synthetic(matrix=[[1.0, None], [None, 0.5]])
    trace = charts._build_heatmap_trace(data, "concept")

    assert trace.z[0][1] is None                # 缺失格保留 None
    assert trace.zmin == -1.0                   # max_abs 取 1.0
    assert trace.zmax == 1.0


def test_build_heatmap_trace_zero_floor():
    """全 0 矩阵：max_abs 下限 0.1，防 zmin==zmax 引发 plotly 异常"""
    trace = charts._build_heatmap_trace(_synthetic(matrix=[[0.0, 0.0]]), "industry")
    assert trace.zmin == -0.1
    assert trace.zmax == 0.1


# ==================== 写盘 ====================

def test_generate_both_sides_writes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(charts.dataflow, "get_industry_daily_returns_matrix",
                        MagicMock(return_value=_synthetic()))
    monkeypatch.setattr(charts.dataflow, "get_concept_daily_returns_matrix",
                        MagicMock(return_value=_synthetic(source="akshare")))

    path = charts.generate_sector_heatmaps(tmp_path)

    assert path is not None
    assert path.name == "sector_daily_heatmaps.html"
    assert (tmp_path / "reports" / "charts" / "sector_daily_heatmaps.html").exists()
    text = path.read_text(encoding="utf-8")
    assert "plotly" in text
    assert "申万一级行业" in text          # 行业口径（tushare）
    assert "东财概念板块" in text          # 概念口径


def test_generate_single_side_writes_single_figure(tmp_path, monkeypatch):
    """仅一侧可用 → 写单图文件，另一侧标题不出现"""
    monkeypatch.setattr(charts.dataflow, "get_industry_daily_returns_matrix",
                        MagicMock(return_value=None))
    monkeypatch.setattr(charts.dataflow, "get_concept_daily_returns_matrix",
                        MagicMock(return_value=_synthetic(source="akshare")))

    path = charts.generate_sector_heatmaps(tmp_path)

    assert path is not None and path.exists()
    text = path.read_text(encoding="utf-8")
    assert "东财概念板块" in text
    assert "申万一级行业" not in text


def test_generate_both_unavailable_returns_none(tmp_path, monkeypatch):
    """双侧均不可用 → 返回 None 且不写文件"""
    monkeypatch.setattr(charts.dataflow, "get_industry_daily_returns_matrix",
                        MagicMock(return_value=None))
    monkeypatch.setattr(charts.dataflow, "get_concept_daily_returns_matrix",
                        MagicMock(return_value=None))

    assert charts.generate_sector_heatmaps(tmp_path) is None
    assert not (tmp_path / "reports" / "charts").exists()


def test_generate_short_and_empty_skipped(tmp_path, monkeypatch):
    """空矩阵与 len(dates)<2 → 该侧跳过（另一侧也不可用 → 不写文件）"""
    monkeypatch.setattr(charts.dataflow, "get_industry_daily_returns_matrix",
                        MagicMock(return_value=_synthetic(dates=["20260819"])))
    monkeypatch.setattr(charts.dataflow, "get_concept_daily_returns_matrix",
                        MagicMock(return_value={"source": "tushare", "dates": [],
                                                "names": [], "pct_matrix": []}))

    assert charts.generate_sector_heatmaps(tmp_path) is None


def test_generate_log_dir_none():
    """日志目录未初始化 → 直接返回 None"""
    assert charts.generate_sector_heatmaps(None) is None


def test_generate_exception_swallowed(tmp_path, monkeypatch):
    """生成异常被捕获 → 返回 None，不抛出"""
    monkeypatch.setattr(charts.dataflow, "get_industry_daily_returns_matrix",
                        MagicMock(side_effect=RuntimeError("boom")))

    assert charts.generate_sector_heatmaps(tmp_path) is None
