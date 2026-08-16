"""AI/templates/ 输出格式模板加载模块的单元测试。"""

import pytest

from AI.templates import load_output_format


def test_load_output_format_returns_stripped_content(tmp_path, monkeypatch):
    """临时 md 文件可正常读取，且首尾空白被去除。"""
    (tmp_path / "market").mkdir()
    md_file = tmp_path / "market" / "test_agent.md"
    md_file.write_text("\n\n## 一、测试章节\n请使用中文。\n\n", encoding="utf-8")

    monkeypatch.setattr("AI.templates._TEMPLATES_DIR", tmp_path)

    result = load_output_format("market", "test_agent")
    assert result == "## 一、测试章节\n请使用中文。"


def test_load_output_format_missing_file_raises(tmp_path, monkeypatch):
    """文件不存在时直接抛 FileNotFoundError，不做静默兜底。"""
    (tmp_path / "market").mkdir()
    monkeypatch.setattr("AI.templates._TEMPLATES_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        load_output_format("market", "not_exist_agent")


def test_load_output_format_utf8_chinese(tmp_path, monkeypatch):
    """中文内容按 utf-8 正确读取（Windows 默认 cp936 会读乱，必须显式编码）。"""
    (tmp_path / "stock").mkdir()
    md_file = tmp_path / "stock" / "cn_agent.md"
    md_file.write_text("# 中国市场技术分析报告\n\n## 一、主要指数量价分析", encoding="utf-8")

    monkeypatch.setattr("AI.templates._TEMPLATES_DIR", tmp_path)

    result = load_output_format("stock", "cn_agent")
    assert "中国市场技术分析报告" in result
    assert "主要指数量价分析" in result


def test_all_real_templates_loadable():
    """全部 14 个真实模板文件可加载且非空（防止改名/删除后悄悄失效）。"""
    templates = {
        "market": [
            "tech_common",
            "news_common",
            "cn_news_analyst",
            "cn_tech_analyst",
            "international_event_extraction",
            "international_event_extraction_final",
            "international_news_analyst",
        ],
        "sector": [
            "sector_news_analyst",
            "sector_tech_analyst",
            "sector_rotation_analyst",
        ],
        "stock": [
            "news_analyst",
            "social_media_analyst",
            "market_analyst",
            "fundamentals_analyst",
        ],
    }
    for layer, names in templates.items():
        for name in names:
            content = load_output_format(layer, name)
            assert content, f"模板 {layer}/{name}.md 为空"
