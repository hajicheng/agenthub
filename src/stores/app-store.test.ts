import { beforeEach, describe, expect, it } from 'vitest'

import type { MessageRow } from '@/db/schema'
import type { DispatchPlanItem } from '@/shared/types'

import { selectDispatchForMessage, useAppStore } from './app-store'

const PLAN: DispatchPlanItem[] = [
  {
    id: 'task_frontend',
    agentId: 'ag_frontend',
    task: '实现页面调整',
  },
]

function resetStore(): void {
  useAppStore.setState({
    conversations: {},
    agents: {},
    messages: {},
    artifacts: {},
    messageIdsByConv: {},
    runsByConv: {},
    dispatches: {},
    activeConversationId: null,
    previewArtifactId: null,
    fileExplorerOpen: false,
    openFilesByConv: {},
    activeTabByConv: {},
    replyTargetByConv: {},
    pendingQuoteForInput: null,
    pendingAttachmentsByConv: {},
    pendingWritesByConv: {},
    pendingBashCommandsByConv: {},
    pendingQuestionsByConv: {},
    unreadByConv: {},
    mobileSidebarOpen: false,
    highlightedMessageId: null,
    streamConnected: false,
  })
}

function agentMessage(id: string, runId: string, createdAt: number): MessageRow {
  return {
    id,
    conversationId: 'conv_1',
    role: 'agent',
    agentId: 'ag_orchestrator',
    parts: [],
    status: 'complete',
    parentMessageId: null,
    mentionedAgentIds: [],
    runId,
    usage: null,
    createdAt,
  }
}

describe('app-store dispatch plan binding', () => {
  beforeEach(() => {
    resetStore()
  })

  it('does not return the same dispatch for every message in the run', () => {
    useAppStore.setState({
      messages: {
        msg_plan: agentMessage('msg_plan', 'run_orch', 1),
        msg_extra: agentMessage('msg_extra', 'run_orch', 2),
      },
      dispatches: {
        run_orch: {
          runId: 'run_orch',
          conversationId: 'conv_1',
          messageId: 'msg_plan',
          plan: PLAN,
          taskStatus: { task_frontend: 'pending' },
          reviewStatus: 'pending',
          pendingPlanId: 'pdp_1',
        },
      },
    })

    const state = useAppStore.getState()
    expect(selectDispatchForMessage(state, 'msg_plan')?.runId).toBe('run_orch')
    expect(selectDispatchForMessage(state, 'msg_extra')).toBeNull()
  })

  it('attaches a pending dispatch to the next message for that run', () => {
    useAppStore.getState().applyEvent({
      type: 'dispatch.plan.pending',
      conversationId: 'conv_1',
      timestamp: 1,
      pendingPlan: {
        id: 'pdp_1',
        conversationId: 'conv_1',
        agentId: 'ag_orchestrator',
        runId: 'run_orch',
        plan: PLAN,
        createdAt: 1,
      },
    })

    expect(useAppStore.getState().dispatches.run_orch?.messageId).toBe('')

    useAppStore.getState().applyEvent({
      type: 'message.start',
      conversationId: 'conv_1',
      timestamp: 2,
      messageId: 'msg_plan',
      agentId: 'ag_orchestrator',
      runId: 'run_orch',
    })

    const state = useAppStore.getState()
    expect(state.dispatches.run_orch?.messageId).toBe('msg_plan')
    expect(selectDispatchForMessage(state, 'msg_plan')?.pendingPlanId).toBe('pdp_1')
  })

  it('keeps successive delegations of the same lead run on their own messages', () => {
    const store = useAppStore.getState()
    const base = { conversationId: 'conv_1', timestamp: 1 }
    store.applyEvent({ ...base, type: 'dispatch.plan', runId: 'run_orch', messageId: 'msg_a', plan: PLAN })
    store.applyEvent({
      ...base, type: 'dispatch.end', parentRunId: 'run_orch', messageId: 'msg_a',
      taskId: PLAN[0].id, childRunId: 'child_a', status: 'complete',
    })
    store.applyEvent({
      ...base, type: 'dispatch.plan.pending',
      pendingPlan: {
        id: 'pdp_b', conversationId: 'conv_1', agentId: 'ag_orchestrator', runId: 'run_orch',
        messageId: 'msg_b', plan: PLAN, createdAt: 2,
      },
    })
    const state = useAppStore.getState()
    expect(selectDispatchForMessage(state, 'msg_a')).toMatchObject({
      childRunId: 'child_a', taskStatus: { task_frontend: 'complete' },
    })
    expect(selectDispatchForMessage(state, 'msg_b')?.pendingPlanId).toBe('pdp_b')
  })
})

describe('app-store run failure cleanup', () => {
  beforeEach(() => {
    resetStore()
  })

  it('adds error results for unresolved tool calls when a run fails', () => {
    useAppStore.setState({
      messages: {
        msg_tool: {
          ...agentMessage('msg_tool', 'run_failed', 1),
          status: 'streaming',
          parts: [
            {
              type: 'tool_use',
              callId: 'call_bash',
              toolName: 'bash',
              args: { command: 'npm run dev' },
            },
          ],
        },
      },
      messageIdsByConv: { conv_1: ['msg_tool'] },
    })

    useAppStore.getState().applyEvent({
      type: 'run.end',
      conversationId: 'conv_1',
      timestamp: 2,
      runId: 'run_failed',
      status: 'failed',
      error: 'process exited with code 1',
    })

    const message = useAppStore.getState().messages.msg_tool
    expect(message.status).toBe('error')
    expect(message.parts).toContainEqual({
      type: 'tool_result',
      callId: 'call_bash',
      result: '工具调用未完成：本次运行失败。process exited with code 1',
      isError: true,
    })

    useAppStore.getState().applyEvent({
      type: 'tool.result',
      conversationId: 'conv_1',
      timestamp: 3,
      messageId: 'msg_tool',
      callId: 'call_bash',
      result: 'server fallback',
      isError: true,
    })

    const results = useAppStore
      .getState()
      .messages.msg_tool.parts.filter((part) => part.type === 'tool_result')
    expect(results).toHaveLength(1)
    expect(results[0]).toEqual({
      type: 'tool_result',
      callId: 'call_bash',
      result: 'server fallback',
      isError: true,
    })
  })

  it('does not duplicate existing tool results on aborted runs', () => {
    useAppStore.setState({
      messages: {
        msg_tool: {
          ...agentMessage('msg_tool', 'run_aborted', 1),
          status: 'streaming',
          parts: [
            {
              type: 'tool_use',
              callId: 'call_done',
              toolName: 'fs_read',
              args: { path: 'README.md' },
            },
            {
              type: 'tool_result',
              callId: 'call_done',
              result: 'ok',
              isError: false,
            },
          ],
        },
      },
      messageIdsByConv: { conv_1: ['msg_tool'] },
    })

    useAppStore.getState().applyEvent({
      type: 'run.end',
      conversationId: 'conv_1',
      timestamp: 2,
      runId: 'run_aborted',
      status: 'aborted',
    })

    const message = useAppStore.getState().messages.msg_tool
    expect(message.status).toBe('aborted')
    expect(message.parts.filter((part) => part.type === 'tool_result')).toHaveLength(1)
  })
})

describe('app-store batching and usage version tracking', () => {
  beforeEach(() => {
    resetStore()
  })

  it('applies batched stream events in order without bumping usageVersion on part.delta', () => {
    const store = useAppStore.getState()
    const base = { conversationId: 'conv_1', timestamp: 1 }

    store.applyEvents([
      { ...base, type: 'message.start', messageId: 'msg_1', agentId: 'ag_1', runId: 'run_1' },
      { ...base, type: 'part.start', messageId: 'msg_1', partIndex: 0, part: { type: 'text', content: '' } },
      { ...base, type: 'part.delta', messageId: 'msg_1', partIndex: 0, delta: { type: 'text.append', text: 'Hello ' } },
      { ...base, type: 'part.delta', messageId: 'msg_1', partIndex: 0, delta: { type: 'text.append', text: 'world' } },
    ])

    const afterDeltas = useAppStore.getState()
    expect(afterDeltas.messages.msg_1?.parts[0]).toEqual({ type: 'text', content: 'Hello world' })
    expect(afterDeltas.usageVersionByConv.conv_1 ?? 0).toBe(0)

    store.applyEvent({
      ...base,
      type: 'message.usage',
      messageId: 'msg_1',
      usage: { inputTokens: 100, outputTokens: 50, cacheReadTokens: 20 },
    })

    expect(useAppStore.getState().usageVersionByConv.conv_1).toBe(1)
  })
})
