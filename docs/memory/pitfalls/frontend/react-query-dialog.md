# React Query / 弹窗交互坑

> 一句话结论：共享 query key 的弹窗订阅用 `enabled` 门控；`RegExp.test` 不得带 `g` 标志；弹窗回填用显式 `userEditedRef` 标记交互。

## refetchOnMount 默认 true（2026-09-08）

- **表象**：同 query key 的观察者晚于首个观察者挂载（如弹窗在数据到达后才挂载）会触发一次额外 refetch（staleTime 0 下数据即陈旧）。
- **正确姿势**：弹窗类共享缓存订阅用 `enabled` 门控（打开才订阅），见 NodeLogsDialog。

## RegExp.test 不得带 g 标志

- **根因**：lastIndex 跨求值残留，交替返回 true/false。
- **正确姿势**：去掉 g，或每次 test 前重置 lastIndex。

## 弹窗回填用显式 userEditedRef 标记交互

- 不得以 text 是否为空推断——清空后 entry refetch 会回写服务端文本，覆盖用户的清空操作。
