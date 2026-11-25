# Spec 17 — 委派审批

主 Agent 调用 `delegate_task` 后，LangGraph 注册待确认的单项任务。前端在发起委派的主消息下显示任务和负责人。

- **批准**后执行已登记的任务。
- **拒绝**将 `rejected` 作为工具结果交回主 Agent。
- **修改**将对话框里的自然语言意见保存为用户消息，并作为 `revision_requested` 的工具结果交回主 Agent。主 Agent 可以依据反馈提交新的任务。
- **中止**会清理待确认项，并向已经启动的子 Run 传递中止信号。

## API 与事件

```text
GET  /api/conversations/:id/pending-dispatch-plans
POST /api/conversations/:id/pending-dispatch-plans/:planId
```

POST 支持 `{ action: 'approve' }`、`{ action: 'reject' }` 和 `{ action: 'revise', feedback: string }`。审批记录包含 `runId`、关联的 `messageId` 和只含一项任务的 `plan`。

后端使用 `dispatch.plan.pending`、`dispatch.plan.resolved`、`dispatch.plan`、`dispatch.start` 与 `dispatch.end` 通知前端。同一主 Run 的不同委派通过 `messageId` 区分。待审批记录的生命周期是当前服务进程，已返回的工具结果随消息保存。
