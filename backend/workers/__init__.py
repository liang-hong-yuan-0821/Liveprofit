"""异步执行面入口：Dramatiq actor、执行器、dispatcher、ingestion jobs。

只消费/投递消息；不复制状态机或直接写业务表（T4 接入）。
"""
