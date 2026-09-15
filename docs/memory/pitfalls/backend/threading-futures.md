# Thread / Future 桥接坑

> 一句话结论：Thread 子类禁用 `_stop` 属性名；concurrent Future 桥接 asyncio 必须 `wrap_future`，超时用 `wait_for(shield(wrapped))`。

## Thread 子类禁用 `_stop` 属性名（2026-09-05）

- **表象**：join 抛 `TypeError: 'Event' object is not callable`。
- **根因**：threading.Thread 内部方法 `_stop()` 在 join 时被调用，子类用 `self._stop = threading.Event()` 覆盖后炸。
- **正确姿势**：心跳/控制线程的事件命名用 `_stop_requested`。

## concurrent.futures.Future 不能直接 wait_for/shield

- 需 `asyncio.wrap_future()` 桥接；超时用 `wait_for(shield(wrapped))` 语义（inner 继续跑，完成回调才释放名额）。
- **连带坑**：`asyncio.run()` 结束后回调 `call_soon_threadsafe` 会抛 `Event loop is closed`——测试必须等名额归零再退出。
