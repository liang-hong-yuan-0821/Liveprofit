"""analysis：分析任务聚合的完整可靠执行闭环。

内部划分 task_lifecycle / reporting / task_events / execution_adapter 四个子域；
对外只公开 Task / Report / Event Contract。
"""
