# V43 任务级 Checkpoint 技术文档

## 问题

旧实现用 `conversation_id` 作为 LangGraph 的线程键。一个会话开始任务 B 时，可能把任务 A 的中断现场合并或覆盖，造成字段串线和错误恢复。

## 设计

v43 将任务身份、待补充请求和 Checkpoint 绑定到同一主键：

`conversation -> TaskSession -> checkpoint_thread_id -> LangGraph checkpoints`

`checkpoint_thread_id` 默认等于 `task_session_id`，并持久化在任务表和待补充记录中。恢复操作必须携带该键，不能根据“最近一条会话消息”猜测。

## 生命周期

1. 新任务创建 `TaskSession` 和独立线程。
2. 缺少信息时，LangGraph interrupt 与 pending 记录同时保留。
3. 用户开始任务 B，任务 A 标记为 suspended，但 A 的 checkpoint 不删除。
4. 用户引用 A 时，路由器定位 A，使用 A 的线程恢复。
5. A 恢复期间，B 的 checkpoint 行数和内容保持不变。

## 失败收敛

- 找不到目标任务时不恢复任何线程。
- pending 与 TaskSession 不属于同一会话时拒绝写入。
- 普通消息使用 `message_<turn_id>` 临时线程。
- checkpoint 键由服务端任务记录提供，不接受用户任意注入。

## 测试证据

`tests/test_v43_task_checkpoints.py` 同时创建两个线程，恢复 A 后直接查询 SQLite，验证 B 未被修改。
