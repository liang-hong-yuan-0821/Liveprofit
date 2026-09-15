# Dramatiq 在 Windows 上的坑

> 一句话结论：Windows 上 Dramatiq 必须走进程内模型（`Worker.start()` + 主线程保活循环），不能用 CLI WorkerProcess；CLI 传参必须改 `sys.argv`。

## CLI WorkerProcess 认证丢失（2026-09-05）

- **表象**：consumer 反复报 "HELLO must be called with the client already authenticated"（主进程 ping 正常、诊断日志密码正常也一样）。
- **根因**：CLI 的 spawn 子进程经 pickle 传递 RedisBroker，认证信息丢失。
- **正确姿势**：进程内模型：

  ```python
  Worker(broker, queues=["default"], worker_threads=1, worker_timeout=1000)
  worker.start()  # start() 不阻塞
  while running: sleep(1)  # 主线程保活；join() 语义不是等待消费
  ```

- **连带坑**：dramatiq 1.17 `broker.enqueue` 只收 Message，投递必须走 `actor.send(...)`；队列实际 key 为 `dramatiq:default`（带前缀）。

## CLI 传参（main(args) 的 args 会被直接当 Namespace 用）

- **表象**：`dramatiq.cli.main(args)` 里 `args.path` AttributeError。
- **正确姿势**：`sys.argv = ["liveprofit-worker", "模块", "--processes", ...]` 后无参调用 `main()`，且以 try/except SystemExit 收口。
