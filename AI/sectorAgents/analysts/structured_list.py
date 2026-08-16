"""
板块层结构化清单提取（选股层机器消费端，统一东财概念体系）

分析师在结论块输出 `结构化清单: ["板块A", "板块B"]` 行，
代码提取后经东财概念全名单过滤（未命中落选），实现
"取消申万行业、统一东财概念"而不改板块层自身数据体系。

安全降级：
- 缺行 / 坏 JSON / 非数组 → 返回 []
- 东财概念名单接口不可用 → 跳过过滤（候选名原样进入，
  选股层按成分股未命中跳过兜底）
"""

import json
import logging
import re

from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)

_STRUCTURED_LIST_PATTERN = re.compile(r'结构化清单\s*[：:]\s*(\[[^\]]*\])')

# 名单接口错误返回的关键词（命中即视为接口不可用）
_NAME_LIST_ERROR_KEYWORDS = (
    "未连接", "未获取", "无法获取", "未安装", "失败", "不支持", "数据不可用", "名单为空",
)


def _parse_concept_names(names_text: str):
    """解析东财概念全名单 → set[str]；接口不可用返回 None"""
    if not names_text:
        return None
    if any(kw in names_text for kw in _NAME_LIST_ERROR_KEYWORDS):
        return None
    return {line.strip() for line in names_text.splitlines() if line.strip()}


def extract_sector_structured_list(report: str) -> list:
    """从报告结论块提取 `结构化清单: [...]` 行并经东财概念名单过滤。"""
    if not report or len(report) < 20:
        return []

    try:
        match = _STRUCTURED_LIST_PATTERN.search(report)
        if not match:
            return []
        candidates = json.loads(match.group(1))
        if not isinstance(candidates, list):
            logger.warning("结构化清单不是 JSON 数组，忽略: %r", candidates)
            return []
        candidates = [str(c).strip() for c in candidates if str(c).strip()]
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"结构化清单解析失败: {e}")
        return []
    if not candidates:
        return []

    valid = _parse_concept_names(dataflow.get_concept_board_names())
    if valid is None:
        logger.warning("东财概念名单不可用，跳过过滤（候选名原样进入，选股层兜底）")
        return _dedupe(candidates)

    dropped = [c for c in _dedupe(candidates) if c not in valid]
    if dropped:
        logger.info(f"结构化清单过滤落选（非东财概念名）: {dropped}")
    return _dedupe([c for c in candidates if c in valid])


def merge_sector_structured_lists(existing: list, new: list) -> list:
    """合并 news/tech 两个分析师的结构化清单（去重，保持顺序）"""
    return _dedupe(list(existing or []) + list(new or []))


def _dedupe(items: list) -> list:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
