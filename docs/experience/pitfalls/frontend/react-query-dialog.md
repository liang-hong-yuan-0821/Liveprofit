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


## 编辑快照必须跨过父组件刷新边界（2026-09-22）

- 原始 DTO 与版本在开始编辑时一起冻结，refetch 不更新提交基线；409 显式放弃后才重载。
- 仅冻结子组件不够：父组件 key=version 会卸载重建弹窗，后台 isError 提前返回也会丢草稿。列表 key 用稳定 ID；只在无缓存时阻断，有缓存的失败旁路提示。
- 验收从真实父组件开始，先编辑、再更新 query cache / 模拟 refetch 失败，等错误提示出现后断言弹窗和草稿仍在。只重渲染子组件无法覆盖卸载问题。
