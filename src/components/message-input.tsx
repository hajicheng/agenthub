'use client'

import {
  Archive,
  AlertTriangle,
  Bot,
  CircleHelp,
  Download,
  Paperclip,
  Rocket,
  Send,
  Settings,
  Shield,
  Sparkles,
  Square,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { nanoid } from 'nanoid'
import { memo, useEffect, useMemo, useRef, useState } from 'react'

import { AttachmentChip, PendingAttachmentChip } from '@/components/attachment-chip'
import { QuotedMessage } from '@/components/quoted-message'
import { SlashCommandHelpDialog } from '@/components/slash-command-help-dialog'
import { SlashCommandMenu, type SlashCommandItem } from '@/components/slash-command-menu'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import type { AgentRow, ConversationWithMeta, MessageRow } from '@/db/schema'
import {
  abortRun,
  clearConversationHistory as clearConversationHistoryAPI,
  compactConversation as compactConversationAPI,
  fetchMessages,
  reviseDispatchPlan,
  sendMessage as sendMessageAPI,
  setFsWriteApprovalMode,
  uploadAttachment as uploadAttachmentAPI,
} from '@/lib/api'
import { getToolDisplayName } from '@/lib/tool-display'
import { emitUiCommand } from '@/lib/ui-command-events'
import { cn } from '@/lib/utils'
import { useAppStore, usePendingAttachments, usePendingPlanReviewForConversation, useTopLevelRunningRuns } from '@/stores/app-store'

interface SlashTrigger {
  start: number
  query: string
}

const SLASH_COMMANDS: SlashCommandItem[] = [
  {
    id: 'deploy',
    command: '/deploy',
    label: '部署产物',
    description: '部署当前会话的网页产物',
    icon: Rocket,
  },
  {
    id: 'compact',
    command: '/compact',
    label: '压缩上下文',
    description: '将早期对话压缩成后续模型读取的摘要',
    icon: Archive,
  },
  {
    id: 'help',
    command: '/help',
    label: '命令帮助',
    description: '查看可用命令',
    icon: CircleHelp,
  },
  {
    id: 'export',
    command: '/export',
    label: '导出会话',
    description: '下载当前会话的 Markdown 记录',
    icon: Download,
  },
  {
    id: 'clear',
    command: '/clear',
    label: '清空历史',
    description: '删除当前会话历史消息',
    icon: Trash2,
  },
  {
    id: 'settings',
    command: '/settings',
    label: '设置',
    description: '打开设置',
    icon: Settings,
  },
  {
    id: 'agents',
    command: '/agents',
    label: 'Agents',
    description: '打开 Agent 管理',
    icon: Bot,
  },
]

function buildConversationExportMarkdown({
  agents,
  conversation,
  conversationId,
  messages,
}: {
  agents: Record<string, AgentRow>
  conversation: ConversationWithMeta | undefined
  conversationId: string
  messages: MessageRow[]
}): string {
  const title = conversation?.title ?? conversationId
  const lines = [
    `# ${title}`,
    '',
    `- Conversation ID: ${conversationId}`,
    `- Exported At: ${new Date().toISOString()}`,
    `- Messages: ${messages.length}`,
    '',
  ]

  messages.forEach((message, index) => {
    lines.push(`## ${index + 1}. ${messageAuthor(message, agents)}`)
    lines.push('')
    lines.push(`_Status: ${message.status} | Created: ${new Date(message.createdAt).toISOString()}_`)
    lines.push('')
    for (const part of message.parts) {
      lines.push(renderMessagePartForExport(part))
      lines.push('')
    }
  })

  return lines.join('\n').trimEnd() + '\n'
}

function messageAuthor(message: MessageRow, agents: Record<string, AgentRow>): string {
  if (message.role === 'user') return 'User'
  if (message.role === 'system') return 'System'
  return message.agentId ? (agents[message.agentId]?.name ?? `Agent ${message.agentId}`) : 'Agent'
}

function renderMessagePartForExport(part: MessageRow['parts'][number]): string {
  switch (part.type) {
    case 'text':
      return part.content
    case 'thinking':
      return `> Thinking\n>\n${blockquote(part.content)}`
    case 'code':
      return ['```' + (part.language ?? ''), part.content, '```'].join('\n')
    case 'tool_use':
      return [
        `Tool Use: ${getToolDisplayName(part.toolName)} (${part.toolName})`,
        '```json',
        stringifyForExport(part.args),
        '```',
      ].join('\n')
    case 'tool_result':
      return [
        `Tool Result${part.isError ? ' (error)' : ''}: ${part.callId}`,
        '```json',
        stringifyForExport(part.result),
        '```',
      ].join('\n')
    case 'artifact_ref':
      return `[Artifact: ${part.artifactId}]`
    case 'deploy_status':
      return part.deployment.status === 'ready'
        ? `[Deployment: ${part.deployment.title} ${formatDeploymentSourceLabel(part.deployment)} (${part.deployment.previewPath})]`
        : `[Deployment failed: ${part.deployment.title} (${part.deployment.error ?? 'unknown error'})]`
    case 'deploy_candidates':
      return `[Deployment candidates: ${part.candidates
        .map((candidate) => `${candidate.title} v${candidate.version} (${candidate.artifactId})`)
        .join(', ')}]`
    case 'image_attachment':
    case 'file_attachment':
      return `[Attachment: ${part.fileName} (${part.attachmentId}, ${part.mimeType}, ${part.size} bytes)]`
  }
}

function formatDeploymentSourceLabel(
  deployment: Extract<MessageRow['parts'][number], { type: 'deploy_status' }>['deployment'],
): string {
  if (deployment.sourceType === 'workspace') {
    return `workspace=${deployment.workspacePath ?? 'unknown'}`
  }
  return `v${deployment.version}`
}

function blockquote(text: string): string {
  return text
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n')
}

function stringifyForExport(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function downloadMarkdownFile(title: string, content: string): void {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  const fileName = `${safeFileName(title)}-${timestamp}.md`
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function safeFileName(value: string): string {
  const cleaned = value.replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim()
  return (cleaned || 'conversation').slice(0, 80)
}

function MessageInputImpl({ conversationId }: { conversationId: string }) {
  const [content, setContent] = useState('')
  const [slashTrigger, setSlashTrigger] = useState<SlashTrigger | null>(null)
  const [slashHighlight, setSlashHighlight] = useState(0)
  const [slashHelpOpen, setSlashHelpOpen] = useState(false)
  const [clearHistoryOpen, setClearHistoryOpen] = useState(false)
  const [clearingHistory, setClearingHistory] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)
  const [aborting, setAborting] = useState(false)
  const [uploading, setUploading] = useState<Array<{ tempId: string; name: string }>>([])

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addLocalUserMessage = useAppStore((s) => s.addLocalUserMessage)
  const upsertMessage = useAppStore((s) => s.upsertMessage)
  const replaceLocalMessageId = useAppStore((s) => s.replaceLocalMessageId)
  const removeMessages = useAppStore((s) => s.removeMessages)
  const clearConversationHistory = useAppStore((s) => s.clearConversationHistory)
  const conversation = useAppStore((s) => s.conversations[conversationId])
  const upsertConversation = useAppStore((s) => s.upsertConversation)
  const agents = useAppStore((s) => s.agents)
  const runningRuns = useTopLevelRunningRuns(conversationId)
  // 委派待审批时，输入框接收用户的修改意见。
  const planReview = usePendingPlanReviewForConversation(conversationId)
  const isRunning = runningRuns.length > 0 || planReview !== null
  const composerLocked = isRunning && !planReview
  const pending = usePendingAttachments(conversationId)
  const addPendingAttachment = useAppStore((s) => s.addPendingAttachment)
  const removePendingAttachment = useAppStore((s) => s.removePendingAttachment)
  const clearPendingAttachments = useAppStore((s) => s.clearPendingAttachments)
  const [modeBusy, setModeBusy] = useState(false)

  // 引用回复目标
  const replyTargetId = useAppStore((s) => s.replyTargetByConv[conversationId])
  const replyMessage = useAppStore((s) => (replyTargetId ? s.messages[replyTargetId] : null))
  const setReplyTarget = useAppStore((s) => s.setReplyTarget)
  const pendingQuote = useAppStore((s) => s.pendingQuoteForInput)
  const setPendingQuote = useAppStore((s) => s.setPendingQuote)

  // 拿到 pendingQuote 后聚焦输入框，方便用户立刻输指令
  useEffect(() => {
    if (pendingQuote) textareaRef.current?.focus()
  }, [pendingQuote])

  const isGroup = conversation?.mode === 'group'

  const slashCommands = useMemo<SlashCommandItem[]>(
    () =>
      SLASH_COMMANDS.map((command) => {
        if (command.id === 'deploy') {
          return {
            ...command,
            description:
              pending.length > 0 || uploading.length > 0
                ? '请先移除附件'
                : isRunning
                  ? '请先中止正在运行的 Agent'
                  : command.description,
            disabled: sending || isRunning || pending.length > 0 || uploading.length > 0,
          }
        }
        if (command.id === 'compact') {
          return {
            ...command,
            description:
              pending.length > 0 || uploading.length > 0
                ? '请先移除附件'
                : command.description,
            disabled: sending || isRunning || pending.length > 0 || uploading.length > 0,
          }
        }
        if (command.id === 'export') {
          return {
            ...command,
            description: exporting ? '正在导出当前会话' : command.description,
            disabled: exporting,
          }
        }
        if (command.id === 'clear') {
          return {
            ...command,
            description: isRunning
              ? '请先中止正在运行的 Agent'
              : clearingHistory
                ? '正在清空会话历史'
                : command.description,
            disabled: isRunning || clearingHistory,
          }
        }
        return command
      }),
    [clearingHistory, exporting, isRunning, pending.length, sending, uploading.length],
  )

  const filteredSlashCommands = useMemo(() => {
    if (!slashTrigger) return []
    const q = slashTrigger.query.toLowerCase()
    if (!q) return slashCommands
    return slashCommands.filter((command) =>
      [
        command.id,
        command.command,
        command.command.slice(1),
        command.label,
        command.description,
      ].some((value) => value.toLowerCase().includes(q)),
    )
  }, [slashTrigger, slashCommands])

  useEffect(() => {
    setSlashHighlight(0)
  }, [slashTrigger?.query, filteredSlashCommands.length])

  // 切换会话清空 state（pending 由 store 自己分桶，不需要在这里清）
  useEffect(() => {
    setContent('')
    setSendError(null)
    setSlashTrigger(null)
    setUploading([])
  }, [conversationId])

  const detectSlashTrigger = (text: string, cursor: number): SlashTrigger | null => {
    const beforeCursor = text.slice(0, cursor)
    const slashIndex = beforeCursor.lastIndexOf('/')
    if (slashIndex < 0) return null
    if (beforeCursor.slice(0, slashIndex).trim().length > 0) return null

    const query = beforeCursor.slice(slashIndex + 1)
    if (/\s/.test(query)) return null
    return { start: slashIndex, query }
  }

  const updateInputTriggers = (text: string, cursor: number) => {
    const next = detectSlashTrigger(text, cursor)
    setSlashTrigger((prev) => {
      if (prev === null && next === null) return prev
      if (prev && next && prev.start === next.start && prev.query === next.query) return prev
      return next
    })
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value
    setContent(value)
    updateInputTriggers(value, e.target.selectionStart)
  }

  // 光标移动（鼠标点击 / 方向键）也要重新判断
  const handleSelect = () => {
    const cursor = textareaRef.current?.selectionStart ?? 0
    updateInputTriggers(content, cursor)
  }

  const removePending = (id: string) => {
    removePendingAttachment(conversationId, id)
  }

  const handleFileSelect = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    const placeholders = list.map((f) => ({ tempId: nanoid(), name: f.name }))
    setUploading((prev) => [...prev, ...placeholders])

    await Promise.all(
      list.map(async (file, i) => {
        const tempId = placeholders[i].tempId
        try {
          const att = await uploadAttachmentAPI(conversationId, file)
          addPendingAttachment(conversationId, att)
        } catch (err) {
          console.error('[MessageInput] upload failed', err)
        } finally {
          setUploading((prev) => prev.filter((p) => p.tempId !== tempId))
        }
      }),
    )
  }

  const clearSlashCommandInput = () => {
    setContent('')
    setSlashTrigger(null)
  }

  const clearComposerDraft = () => {
    clearSlashCommandInput()
    clearPendingAttachments(conversationId)
    if (pendingQuote) setPendingQuote(null)
    if (replyTargetId) setReplyTarget(conversationId, null)
  }

  const executeExportCommand = async () => {
    if (exporting) return
    clearSlashCommandInput()
    setExporting(true)
    try {
      const messages = await fetchMessages(conversationId)
      const markdown = buildConversationExportMarkdown({
        agents,
        conversation,
        conversationId,
        messages,
      })
      downloadMarkdownFile(conversation?.title ?? conversationId, markdown)
    } catch (err) {
      console.error('[MessageInput] export failed', err)
    } finally {
      setExporting(false)
    }
  }

  const confirmClearHistory = async () => {
    if (clearingHistory || isRunning) return
    setClearingHistory(true)
    try {
      const result = await clearConversationHistoryAPI(conversationId)
      clearConversationHistory(conversationId, result.conversation)
      clearComposerDraft()
      setClearHistoryOpen(false)
    } catch (err) {
      console.error('[MessageInput] clear history failed', err)
    } finally {
      setClearingHistory(false)
    }
  }

  const executeCompactCommand = async () => {
    if (sending || isRunning || pending.length > 0 || uploading.length > 0) return
    clearSlashCommandInput()
    if (pendingQuote) setPendingQuote(null)
    if (replyTargetId) setReplyTarget(conversationId, null)
    setSending(true)
    try {
      const result = await compactConversationAPI(conversationId)
      upsertMessage(result.message)
    } catch (err) {
      console.error('[MessageInput] compact failed', err)
    } finally {
      setSending(false)
    }
  }

  const executeDeployCommand = async () => {
    if (sending || isRunning || pending.length > 0 || uploading.length > 0) return
    clearSlashCommandInput()
    if (pendingQuote) setPendingQuote(null)
    if (replyTargetId) setReplyTarget(conversationId, null)

    const tempId = `temp_${nanoid()}`
    addLocalUserMessage({
      tempId,
      conversationId,
      content: '/deploy',
      attachments: [],
    })
    setSending(true)
    try {
      const result = await sendMessageAPI(conversationId, { content: '/deploy' })
      replaceLocalMessageId(tempId, result.messageId)
      upsertReturnedMessages(result.messages)
    } catch (err) {
      console.error('[MessageInput] deploy failed', err)
    } finally {
      setSending(false)
    }
  }

  const upsertReturnedMessages = (messages: MessageRow[] | undefined) => {
    for (const message of messages ?? []) upsertMessage(message)
  }

  const executeSlashCommand = async (command: SlashCommandItem) => {
    if (command.disabled) return
    switch (command.id) {
      case 'deploy':
        await executeDeployCommand()
        break
      case 'compact':
        await executeCompactCommand()
        break
      case 'help':
        clearSlashCommandInput()
        setSlashHelpOpen(true)
        break
      case 'export':
        await executeExportCommand()
        break
      case 'clear':
        clearSlashCommandInput()
        setClearHistoryOpen(true)
        break
      case 'settings':
        clearSlashCommandInput()
        emitUiCommand('open-settings')
        break
      case 'agents':
        clearSlashCommandInput()
        emitUiCommand('open-agents')
        break
      default:
        break
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashTrigger && filteredSlashCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashHighlight((i) => (i + 1) % filteredSlashCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashHighlight(
          (i) => (i - 1 + filteredSlashCommands.length) % filteredSlashCommands.length,
        )
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const command = filteredSlashCommands[slashHighlight]
        if (command) void executeSlashCommand(command)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashTrigger(null)
        return
      }
    }
    // 默认 Enter 提交
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  const submit = async () => {
    const text = content.trim()
    const hasAttachments = pending.length > 0

    // 委派审批中，输入作为修改意见交回主 Agent。
    // 反馈会由服务端落库 + 广播成一条 user 消息回显到对话。
    if (planReview) {
      if (!text || sending) return
      setSendError(null)
      setContent('')
      setSending(true)
      try {
        await reviseDispatchPlan(conversationId, planReview.planId, text)
      } catch (err) {
        setContent((current) => current || text)
        setSendError(err instanceof Error ? err.message : String(err))
      } finally {
        setSending(false)
      }
      return
    }

    if ((!text && !hasAttachments) || sending || isRunning) return
    setSendError(null)

    const exactSlashCommand = slashCommands.find((command) => command.command === text)
    if (exactSlashCommand) {
      await executeSlashCommand(exactSlashCommand)
      return
    }

    // 选区改写：把 pendingQuote 注入消息开头（XML 块给 LLM 当上下文）
    const finalContent = pendingQuote
      ? `<quoted_selection source="${pendingQuote.sourceLabel}"${pendingQuote.artifactId ? ` artifactId="${pendingQuote.artifactId}"` : ''}${pendingQuote.filePath ? ` filePath="${pendingQuote.filePath}"` : ''}>\n${pendingQuote.text}\n</quoted_selection>\n\n${text}`
      : text

    const tempId = `temp_${nanoid()}`
    const parentId = replyTargetId ?? undefined
    addLocalUserMessage({
      tempId,
      conversationId,
      content: finalContent,
      parentMessageId: parentId,
      attachments: pending,
    })
    setContent('')
    setSlashTrigger(null)
    if (pendingQuote) setPendingQuote(null)
    const attachmentIds = pending.map((a) => a.id)
    clearPendingAttachments(conversationId)
    if (replyTargetId) setReplyTarget(conversationId, null)
    setSending(true)

    try {
      const result = await sendMessageAPI(conversationId, {
        content: finalContent,
        parentMessageId: parentId,
        attachmentIds,
      })
      replaceLocalMessageId(tempId, result.messageId)
      upsertReturnedMessages(result.messages)
    } catch (err) {
      removeMessages(conversationId, [tempId])
      setContent((current) => current || text)
      for (const attachment of pending) addPendingAttachment(conversationId, attachment)
      if (pendingQuote) setPendingQuote(pendingQuote)
      if (replyTargetId) setReplyTarget(conversationId, replyTargetId)
      setSendError(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  const abortAll = async () => {
    if (aborting) return
    setAborting(true)
    try {
      const runIds = new Set(runningRuns.map((run) => run.id))
      if (planReview) runIds.add(planReview.runId)
      await Promise.allSettled(Array.from(runIds, abortRun))
    } finally {
      setAborting(false)
    }
  }

  const approvalMode = conversation?.fsWriteApprovalMode ?? 'review'
  const toggleApprovalMode = async () => {
    if (modeBusy || !conversation) return
    const nextMode = approvalMode === 'review' ? 'auto' : 'review'
    setModeBusy(true)
    try {
      const updated = await setFsWriteApprovalMode(conversationId, nextMode)
      upsertConversation(updated)
    } catch (err) {
      console.error('[MessageInput] toggle approval mode failed', err)
    } finally {
      setModeBusy(false)
    }
  }

  return (
    <div className="relative shrink-0 border-t bg-background p-3">
      {/* 引用预览 */}
      {replyMessage && (
        <div className="mb-2">
          <QuotedMessage
            message={replyMessage}
            variant="compose"
            onDismiss={() => setReplyTarget(conversationId, null)}
          />
        </div>
      )}

      {/* 选区改写引用块 */}
      {pendingQuote && (
        <div className="mb-2 flex items-start gap-2 rounded-md border border-[#3370FF]/30 bg-[#3370FF]/5 px-2 py-1.5 text-xs">
          <Sparkles className="mt-0.5 size-3 shrink-0 text-[#3370FF]" />
          <div className="min-w-0 flex-1">
            <div className="font-medium text-[#3370FF]">
              {pendingQuote.kind === 'ask' ? '提问' : '改写'} · {pendingQuote.sourceLabel}
            </div>
            <pre className="mt-0.5 line-clamp-3 whitespace-pre-wrap break-words font-mono text-[10px] text-muted-foreground">
              {pendingQuote.text}
            </pre>
            <div className="mt-0.5 text-[10px] text-muted-foreground/70">
              {pendingQuote.kind === 'ask'
                ? '在下方输入框写你的问题，发送时会带上这段引用一起发给 Agent'
                : '在下方输入框写改写指令，发送时会作为引用一起发给 Agent'}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setPendingQuote(null)}
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="取消引用"
          >
            <X className="size-3" />
          </button>
        </div>
      )}

      {/* Attachments chips */}
      {(pending.length > 0 || uploading.length > 0) && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pending.map((a) => (
            <AttachmentChip
              key={a.id}
              attachment={{
                id: a.id,
                fileName: a.fileName,
                size: a.size,
                mimeType: a.mimeType,
                kind: a.kind,
              }}
              context="compose"
              onRemove={() => removePending(a.id)}
            />
          ))}
          {uploading.map((u) => (
            <PendingAttachmentChip key={u.tempId} fileName={u.name} />
          ))}
        </div>
      )}

      {clearHistoryOpen && (
        <Dialog
          open={clearHistoryOpen}
          onOpenChange={(open) => {
            if (!clearingHistory) setClearHistoryOpen(open)
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AlertTriangle className="size-4 text-destructive" />
                清空会话历史？
              </DialogTitle>
              <DialogDescription>
                将删除当前会话的所有历史消息、运行记录和上下文压缩摘要，无法撤销。产物、附件和
                workspace 文件不会被删除。
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={clearingHistory}
                onClick={() => setClearHistoryOpen(false)}
              >
                取消
              </Button>
              <Button
                type="button"
                variant="destructive"
                disabled={clearingHistory || isRunning}
                onClick={() => void confirmClearHistory()}
              >
                {clearingHistory ? '清空中...' : '清空历史'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {slashHelpOpen && (
        <SlashCommandHelpDialog
          open={slashHelpOpen}
          commands={SLASH_COMMANDS}
          onOpenChange={setSlashHelpOpen}
        />
      )}

      <SlashCommandMenu
        commands={slashTrigger ? filteredSlashCommands : []}
        highlightedIndex={slashHighlight}
        onHighlight={setSlashHighlight}
        onSelect={(command) => void executeSlashCommand(command)}
      />

      {sendError && <p role="alert" className="text-xs text-destructive">{sendError}</p>}

      <div className="flex items-center gap-2">
        <Textarea
          ref={textareaRef}
          data-testid="composer-input"
          value={content}
          onChange={handleChange}
          onSelect={handleSelect}
          onKeyDown={handleKeyDown}
          placeholder={
            planReview
              ? '对委派任务提修改意见，或在上方批准或拒绝…'
              : isRunning
                ? '当前有 Agent 正在响应…'
                : isGroup
                  ? '向主 Agent 发送消息，Enter 发送，Shift+Enter 换行'
                  : '输入消息，Enter 发送，Shift+Enter 换行'
          }
          className="min-h-[44px] max-h-40 resize-none"
          disabled={composerLocked}
        />

        {/* 文件上传 */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            void handleFileSelect(e.target.files)
            e.target.value = '' // 允许同名文件再次选择
          }}
        />
        {/* 辅助按钮组（紧贴）—— 让 Paperclip + 审批模式视觉成一组，与右侧主操作按钮 send 区分 */}
        <div className="flex items-center">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={() => fileInputRef.current?.click()}
            disabled={isRunning}
            title="附件 / 图片"
          >
            <Paperclip className="size-4" />
          </Button>
          {/* fs_write 审批模式开关：绿色 = Review（默认安全），红色 = Auto（直写） */}
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={() => void toggleApprovalMode()}
            disabled={modeBusy}
            title={
              approvalMode === 'review'
                ? 'Review 模式 · Agent 写入需审批（点击切到 Auto，直接生效 ⚠）'
                : '⚠ Auto 模式 · Agent 写入直接生效（点击切回 Review）'
            }
            className={cn(
              approvalMode === 'review'
                ? 'text-emerald-600 hover:text-emerald-700 dark:text-emerald-400'
                : 'text-[#FE3B25] hover:text-[#FE3B25] dark:text-[#FE3B25]',
            )}
          >
            {approvalMode === 'review' ? (
              <Shield className="size-4" />
            ) : (
              <Zap className="size-4" />
            )}
          </Button>
        </div>
        {composerLocked ? (
          <Button
            onClick={() => void abortAll()}
            disabled={aborting}
            size="icon"
            variant="destructive"
            title="中止全部"
            data-testid="composer-abort"
          >
            <Square className="size-4 fill-current" />
          </Button>
        ) : (
          <Button
            onClick={() => void submit()}
            disabled={(!content.trim() && pending.length === 0) || sending}
            size="icon"
            title="发送 (Enter)"
            data-testid="composer-send"
          >
            <Send className="size-4" />
          </Button>
        )}
      </div>
    </div>
  )
}

export const MessageInput = memo(MessageInputImpl)
MessageInput.displayName = 'MessageInput'
