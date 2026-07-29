"""
YoHo 信号处理器 (简化版)
从最终交易决策文本中提取结构化的 JSON 决策。

简化：仅支持人民币（A股），移除多货币模式。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


class SignalProcessor:
    """处理交易信号，提取可操作的决策"""

    def __init__(self, quick_thinking_llm):
        self.quick_thinking_llm = quick_thinking_llm

    def process_signal(self, full_signal: str, stock_symbol: str = None) -> dict:
        """
        从完整交易信号中提取结构化决策信息

        Returns:
            包含 action, target_price, confidence, risk_score, reasoning 的字典
        """
        if not full_signal or not isinstance(full_signal, str):
            return self._get_default_decision()

        full_signal = full_signal.strip()
        if len(full_signal) == 0:
            return self._get_default_decision()

        # A股固定使用人民币
        currency = "人民币"
        currency_symbol = "¥"

        logger.info(f"[信号处理] 股票={stock_symbol}, 货币={currency}")

        messages = [
            (
                "system",
                f"""你是一位专业的金融分析助手，负责从分析报告中提取结构化投资决策。

请以JSON格式返回：
{{
    "action": "买入/持有/卖出",
    "target_price": 数字({currency}价格，必须提供具体数值，不能为null),
    "confidence": 数字(0-1之间，默认0.7),
    "risk_score": 数字(0-1之间，默认0.5),
    "reasoning": "决策主要理由摘要"
}}

要求：
1. action必须是"买入"、"持有"或"卖出"之一（不允许英文）
2. target_price必须是具体的{currency}价格数字（{currency_symbol}）
3. 所有内容使用中文""",
            ),
            ("human", full_signal),
        ]

        try:
            response = self.quick_thinking_llm.invoke(messages).content

            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                decision_data = json.loads(json_match.group())

                action = decision_data.get('action', '持有')
                if action not in ['买入', '持有', '卖出']:
                    action_map = {
                        'buy': '买入', 'hold': '持有', 'sell': '卖出',
                        'BUY': '买入', 'HOLD': '持有', 'SELL': '卖出',
                    }
                    action = action_map.get(action, '持有')

                target_price = decision_data.get('target_price')
                if target_price is not None and target_price != "null" and target_price != "":
                    try:
                        target_price = float(target_price)
                    except (ValueError, TypeError):
                        target_price = None

                if target_price is None:
                    target_price = self._extract_price_from_text(full_signal)

                return {
                    'action': action,
                    'target_price': target_price,
                    'confidence': float(decision_data.get('confidence', 0.7)),
                    'risk_score': float(decision_data.get('risk_score', 0.5)),
                    'reasoning': decision_data.get('reasoning', '基于综合分析的投资建议'),
                }
            else:
                return self._extract_simple_decision(full_signal)
        except Exception as e:
            logger.error(f"信号处理错误: {e}")
            return self._extract_simple_decision(full_signal)

    def _extract_price_from_text(self, text: str):
        """从文本中提取价格"""
        patterns = [
            r'目标价[位格]?[：:]?\s*[¥]?(\d+(?:\.\d+)?)',
            r'目标[：:]?\s*[¥]?(\d+(?:\.\d+)?)',
            r'[¥](\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)元',
            r'看[到至]\s*[¥]?(\d+(?:\.\d+)?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    def _extract_simple_decision(self, text: str) -> dict:
        """简单的决策提取备用方法"""
        action = '持有'
        if re.search(r'买入|BUY', text, re.IGNORECASE):
            action = '买入'
        elif re.search(r'卖出|SELL', text, re.IGNORECASE):
            action = '卖出'
        elif re.search(r'持有|HOLD', text, re.IGNORECASE):
            action = '持有'

        target_price = self._extract_price_from_text(text)

        return {
            'action': action,
            'target_price': target_price,
            'confidence': 0.7,
            'risk_score': 0.5,
            'reasoning': '基于综合分析的投资建议',
        }

    def _get_default_decision(self) -> dict:
        return {
            'action': '持有',
            'target_price': None,
            'confidence': 0.5,
            'risk_score': 0.5,
            'reasoning': '输入数据无效，默认持有建议',
        }
