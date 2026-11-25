'use client'

import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { z } from 'zod'

import { AgentAvatar } from '@/components/agent-avatar'
import { PartList } from '@/components/message-parts'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import type { MessageRow } from '@/db/schema'
import { approvePendingDispatchPlan, rejectPendingDispatchPlan } from '@/lib/api'
import type { MessagePart } from '@/shared/types'
import { useAppStore, useChildMessages, type DispatchState } from '@/stores/app-store'

const assignmentSchema = z.object({ agentId: z.string().min(1), task: z.string().min(1) })
const resultSchema = z.object({
  status: z.enum(['complete', 'failed', 'aborted', 'rejected', 'revision_requested']),
  summary: z.string().optional(),
  error: z.string().nullish(),
  feedback: z.string().optional(),
  childRunId: z.string().optional(),
  artifactIds: z.array(z.string()).optional(),
})
const errorSchema = z.object({ error: z.string() })

const STATUS_LABELS = {
  pending: '待确认',
  running: '处理中',
  complete: '已完成',
  failed: '失败',
  aborted: '已中止',
  skipped: '已跳过',
  rejected: '已拒绝',
  revision_requested: '已退回修改',
}

export function getDelegationCall(parts: MessagePart[]) {
  const call = parts.find((part) => part.type === 'tool_use' && part.toolName === 'delegate_task')
  if (call?.type !== 'tool_use') return null
  const assignment = assignmentSchema.safeParse(call.args)
  return assignment.success ? { ...assignment.data, callId: call.callId } : null
}

export function DispatchPlanCard({ message, dispatch }: { message: MessageRow; dispatch: DispatchState | null }) {
  const assignment = getDelegationCall(message.parts)
  const task = dispatch?.plan[0] ?? (assignment ? { ...assignment, id: message.id } : undefined)
  const toolResult = message.parts.find(
    (part) => part.type === 'tool_result' && part.callId === assignment?.callId,
  )
  const parsed = resultSchema.safeParse(toolResult?.type === 'tool_result' ? toolResult.result : undefined)
  const result = parsed.data
  const parsedError = errorSchema.safeParse(toolResult?.type === 'tool_result' ? toolResult.result : undefined)
  const error = result?.error ?? parsedError.data?.error ?? dispatch?.error
  const agent = useAppStore((store) => task ? store.agents[task.agentId] : undefined)
  const childMessages = useChildMessages(message.conversationId, result?.childRunId ?? dispatch?.childRunId)
  const [busy, setBusy] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  if (!task) return null

  const pendingId = dispatch?.reviewStatus === 'pending' ? dispatch.pendingPlanId : undefined
  const status = result?.status ??
    (error ? 'failed' : undefined) ??
    (dispatch?.reviewStatus === 'rejected' || dispatch?.reviewStatus === 'revision_requested'
      ? dispatch.reviewStatus
      : undefined) ??
    dispatch?.taskStatus[task.id] ??
    (message.status === 'streaming' ? 'running' : message.status === 'error' ? 'failed' : 'aborted')

  async function review(action: 'approve' | 'reject') {
    if (busy || !pendingId) return
    setBusy(true)
    setReviewError(null)
    try {
      const submit = action === 'approve' ? approvePendingDispatchPlan : rejectPendingDispatchPlan
      await submit(message.conversationId, pendingId)
    } catch (err) {
      setReviewError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Card className="mt-3 gap-2 p-3 text-sm" data-testid="delegation-card">
      <div className="flex flex-wrap items-center gap-2">
        {agent && <AgentAvatar agent={agent} size="xs" />}
        <span className="font-medium">{agent?.name ?? task.agentId}</span>
        {status === 'running' && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-hidden />}
        <span className="text-xs text-muted-foreground">{STATUS_LABELS[status]}</span>
      </div>
      <p className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{task.task}</p>
      {pendingId && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void review('reject')}>拒绝</Button>
          <Button size="sm" disabled={busy} onClick={() => void review('approve')}>批准任务</Button>
        </div>
      )}
      {(reviewError || error) && <p role="alert" className="text-xs text-destructive">{reviewError ?? error}</p>}
      {result?.feedback && <p className="whitespace-pre-wrap text-xs text-muted-foreground">{result.feedback}</p>}
      {(result?.summary || !!result?.artifactIds?.length || childMessages.length > 0) && (
        <details className="border-t border-border pt-2">
          <summary className="cursor-pointer text-xs font-medium">执行详情</summary>
          <div className="mt-2 space-y-3">
            {childMessages.length > 0
              ? childMessages.map((child) => (
                  <PartList key={child.id} parts={child.parts} conversationId={message.conversationId} />
                ))
              : result?.summary && <PartList parts={[{ type: 'text', content: result.summary }]} conversationId={message.conversationId} />}
            {!!result?.artifactIds?.length && (
              <PartList
                parts={result.artifactIds.map((artifactId) => ({ type: 'artifact_ref', artifactId }))}
                conversationId={message.conversationId}
              />
            )}
          </div>
        </details>
      )}
    </Card>
  )
}
