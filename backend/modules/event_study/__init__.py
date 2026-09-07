"""event_study：同步事件预测、结果归一化与审核宏观信息投影。

作为既有事件研究的防腐边界：复用 predictor.predict_impact()，不复制算法、
不修改既有事件研究表结构（T7 实现 Adapter/应用用例）。
"""

from __future__ import annotations
