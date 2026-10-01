# FastAPI OpenAPI / prometheus_client 坑

> 一句话结论：SSE 端点加 `response_model=SSEContract` 即可把事件 Schema 注册进 OpenAPI；`GaugeMetricFamily` 在 `prometheus_client.metrics_core`。

## 注册 SSE 协议 Schema 进 OpenAPI（2026-09-05）

- **根因**：StreamingResponse 端点的响应 Schema 默认不进 components。
- **正确姿势**：给端点加 `response_model=SSEContract`（文档用途）即可把 BusinessEventData/ResetEventData/HeartbeatEventData 注册进 components，运行时 StreamingResponse 不经 JSON 序列化不受影响；`responses={...content: {schema: <ModelClass>}}` 反而会把 ModelMetaclass 编进 schema 报 PydanticSerializationError。

## prometheus_client 导入路径

- `GaugeMetricFamily` 在 `prometheus_client.metrics_core`，顶层包不导出。
