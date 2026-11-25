/**
 * Pure TypeScript entity types shared by the Next.js frontend.
 * Runtime persistence is handled by the Python FastAPI backend (backend/app/db/).
 */

import type {
  AdapterName,
  ArtifactContent,
  ArtifactType,
  MessagePart,
  ModelProvider,
} from '@/shared/types'

export interface AgentRow {
  id: string
  name: string
  avatar: string
  description: string
  capabilities: string[]
  systemPrompt: string
  adapterName: AdapterName
  modelProvider: ModelProvider | null
  modelId: string | null
  apiKey: string | null
  apiBaseUrl: string | null
  toolNames: string[]
  isBuiltin: boolean
  isOrchestrator: boolean
  supportsVision: boolean
  createdAt: number
}
export type AgentInsert = AgentRow

export interface ConversationRow {
  id: string
  title: string
  mode: 'single' | 'group'
  agentIds: string[]
  pinnedMessageIds: string[]
  bookmarkedMessageIds: string[]
  archived: boolean
  pinnedAt: number | null
  fsWriteApprovalMode: 'auto' | 'review'
  createdAt: number
  updatedAt: number
}
export type ConversationInsert = ConversationRow

export interface ConversationWithMeta extends ConversationRow {
  workspaceMode: 'sandbox' | 'local'
  workspaceBoundPath: string | null
}

export interface MessageUsage {
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
}

export interface MessageRow {
  id: string
  conversationId: string
  role: 'user' | 'agent' | 'system'
  agentId: string | null
  parts: MessagePart[]
  status: 'streaming' | 'complete' | 'error' | 'aborted'
  parentMessageId: string | null
  mentionedAgentIds: string[]
  runId: string | null
  usage: MessageUsage | null
  createdAt: number
}
export type MessageInsert = MessageRow

export interface ArtifactRow {
  id: string
  conversationId: string
  type: ArtifactType
  title: string
  content: ArtifactContent
  version: number
  parentArtifactId: string | null
  createdByAgentId: string
  createdAt: number
}
export type ArtifactInsert = ArtifactRow

export interface WorkspaceRow {
  id: string
  conversationId: string
  rootPath: string
  mode: 'sandbox' | 'local'
  boundPath: string | null
  createdAt: number
}
export type WorkspaceInsert = WorkspaceRow

export interface AttachmentRow {
  id: string
  conversationId: string
  kind: 'image' | 'file'
  fileName: string
  filePath: string
  size: number
  mimeType: string
  createdAt: number
}
export type AttachmentInsert = AttachmentRow

export interface RunUsage {
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
  lastInputTokens?: number
  model?: string
}

export interface AgentRunRow {
  id: string
  conversationId: string
  agentId: string
  triggerMessageId: string | null
  status: 'queued' | 'running' | 'complete' | 'failed' | 'aborted'
  error: string | null
  parentRunId: string | null
  usage: RunUsage | null
  startedAt: number
  finishedAt: number | null
}
export type AgentRunInsert = AgentRunRow

export interface ContextSummaryRow {
  id: string
  conversationId: string
  summary: string
  coveredUntilMessageId: string
  coveredUntilCreatedAt: number
  sourceMessageCount: number
  tokenEstimate: number
  modelProvider: ModelProvider | null
  modelId: string | null
  createdAt: number
}
export type ContextSummaryInsert = ContextSummaryRow

export interface AppSettingsRow {
  id: string
  anthropicApiKey: string | null
  anthropicBaseUrl: string | null
  openaiApiKey: string | null
  deepseekApiKey: string | null
  arkApiKey: string | null
  companionMode: 'off' | 'lan' | 'tailnet'
  mobileDeviceToken: string | null
  deploymentPublishEnabled: boolean
  deploymentPublishDir: string | null
  deploymentPublicBaseUrl: string | null
  updatedAt: number
}
export type AppSettingsInsert = AppSettingsRow
