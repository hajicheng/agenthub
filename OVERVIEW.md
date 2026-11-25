# AgentHub 项目全貌（OVERVIEW）

> **这份文档是给 AI / 新对话窗口的「全貌速览」**：读完它,不翻代码也能掌握项目做了什么、怎么分层、代码在哪、当前进度。
>
> 与其它文档的分工：`OVERVIEW.md` 给**地图**（做了什么 / 代码在哪）· `CLAUDE.md` 定**规则**（怎么做 / 不做什么）· `specs/` 定**规格**（每个模块的字段与契约）· `skills/` 给**配方**（扩展任务步骤化指南）· `docs/AI-COLLABORATION.md` 记**协作方法**（方法论复盘 + 实录佐证）· `README.md` 面向**人类用户**（安装 / 快速开始）。
>
> ⚠️ 下篇「代码地图」相对稳定;「附录·当前现状」会随开发过时 —— **以 `git log` 与代码为准**。最后更新见文末。

---

## 上篇 · 全局认知

### 1. 一句话定位 + 成熟度

> 把多 Agent 协作做成 IM 群聊体验 —— Agent 是「联系人」,对话是「工作空间」,Orchestrator 是「群里的项目经理」。

本地运行（`pnpm dev`,SQLite 文件库,不依赖托管服务）。经 100+ commit 演进,五层架构完整落地,功能闭环已跑通,并已做到 **Electron 桌面打包** + **移动端伴随 App 脚手架**。当前重心是测试覆盖（Playwright E2E）与产物体验打磨（PPT 视觉、产物预览）。

### 2. 五层架构 + 数据流

```
L5 UI 组件（React / shadcn）            src/components/**, src/app/**
   ↑↓
L4 State + Transport                    src/stores/app-store.ts（Zustand+Immer）
   ├ Zustand normalized store           src/components/stream-provider.tsx（SSE 客户端）
   └ SSE 单连接（/api/stream）
   ↑↓
L3 Application Services                  backend/app/
   ├ AgentRunner（run 生命周期）         runner/agent_runner.py
   ├ LangGraph（群聊编排）                runner/orchestration_graph.py
   ├ 会话、审批和 EventBus                services/
   └ 工具注册和执行                      tools/registry.py
   ↑↓
L2 Agent Platform Adapters              backend/app/adapters/
   ├ ClaudeCodeAdapter / CustomAgentAdapter / MockAdapter
   ↑↓
L1 Persistence                          backend/app/db/（SQLite） + workspace 文件系统
```

**数据流主线（一次 Agent 回复）**：
用户发消息 → API 路由 → `AgentRunner` 起 run → 选 `Adapter` 调 LLM → Adapter 吐 **`StreamEvent`** → AgentRunner 持久化 + 经 `EventBus` 推 SSE → 前端 `stream-provider` 收事件 → `app-store` reducer 应用 → UI 重渲染。

**核心契约（改动必读对应 spec）**：
- **`StreamEvent` 联合类型**是粘合全系统的事件协议（`specs/02`）。
- **Message = parts 数组**（text / thinking / tool_use / artifact_ref …），不是 markdown 字符串（`specs/03`）。
- **Artifact 独立于 Message**,有自己的生命周期与版本链（`specs/04`）。
- **Orchestrator 由 AgentRunner 运行**，通过 `delegate_task` 逐项委派（`specs/06`）。

### 3. 功能现状矩阵

| 能力 | 状态 | 说明 |
|---|---|---|
| IM 会话（多会话/搜索/置顶/归档/未读） | ✅ | 单聊 + 主 Agent 群聊 |
| 消息操作（引用/撤回/编辑重发/重新生成/收藏☆/Pin） | ✅ | ☆ 书签跳转+辉光;Pin 注入 LLM 长期上下文(消息按钮 + 顶部横幅) |
| ClaudeCodeAdapter | ✅ | Python Anthropic SDK + AgentHub 工具审批 |
| CustomAgentAdapter | ✅ | OpenAI 兼容（DeepSeek/OpenAI/火山方舟）+ 自驱 tool loop |
| MockAdapter | ✅ | 开发期不烧 token |
| CodexAdapter | ⏳ | FastAPI 适配器会提示切换到 Custom 或 Claude |
| 自建 Agent | ✅ | 表单/对话式创建,自定义 prompt + 工具集 |
| Orchestrator 编排 | ✅ | LangGraph 单项审批与子 Run 执行，结果交回主 Agent 决定下一步 |
| 工具系统 | ✅ | write/deploy/read_artifact · read_attachment · fs_read/fs_write/bash · delegate_task · ask_user |
| Artifact 预览/编辑 | ✅ | web_app(iframe + preview URL + 本地静态发布/源码包/容器包) / document(md) / image / 版本对比 diff（历史 diff 只读兼容） / **ppt(幻灯片分页预览 + 完整 theme token + 导出真 .pptx)** · code_file workspace 预览/编辑 · 版本链 v1↔v2 · 选区改写 · 面板内编辑(CodeMirror)→提交新版本 · 导出 |
| Workspace 沙箱 | ✅ | sandbox/local 双模式 · fs_write 审批(Review/Auto) · 双平台 Bash 黑名单 |
| Token 计量 | ✅ | per-run/per-message · cache 命中率 · 全局分析 Tab |
| 跨 run 对话记忆 | ✅ | 历史序列化注入 · token 预算 · 群聊跨 agent 可见 · 手动压缩 |
| 平台抽象（Win/POSIX） | ✅ | shell 选择 · 多盘符 DirPicker · 子进程清理 |
| Electron 桌面版 | ✅ | DMG / EXE 打包 · userData 路径迁移 |
| 全局 API Key 设置面板 | ✅ | app_settings 单行表 · 三层 key 优先级 |
| 移动端伴随 App | ⏳ | 响应式 Web 已适配;Capacitor 原生壳脚手架已建,配对通信待打通 |
| 斜杠命令菜单 | ✅ | 输入 `/` 弹命令浮层（打开设置 / Agents 库等 UI 命令） |
| 测试覆盖 | 🟡 | Vitest 纯函数（security / workspace-utils / dispatch-plan / artifact-content / ppt-export / ppt-theme）；Playwright **E2E 基建 + 核心 IM 流**（mock agent，见附录）；产物/群聊调度 E2E 待补（需测试假 adapter） |

---

## 下篇 · 代码地图（功能 → 文件）

> 路径相对仓库根。找某功能从这里定位,不用全局搜索。

### 入口 & 前端壳
| 关注点 | 文件 |
|---|---|
| App 入口 / 布局 | `src/app/page.tsx` · `src/app/layout.tsx` |
| SSE 全局连接（客户端） | `src/components/stream-provider.tsx` |
| 前端状态总线（Zustand+Immer，reducer 在此应用 StreamEvent） | `src/stores/app-store.ts` |
| 主题 | `src/components/theme-provider.tsx` · `theme-toggle.tsx` |

### L5 UI 组件（`src/components/`）
| 区域 | 文件 |
|---|---|
| 侧栏（会话/产物库/Agents/分析 Tab） | `sidebar.tsx` |
| 聊天主面板 | `chat-panel.tsx` · `message-list.tsx` · `message-item.tsx` · `message-parts.tsx` |
| 输入框（附件/审批模式/选区引用/斜杠命令） | `message-input.tsx` · `edit-message-input.tsx` |
| Orchestrator 调度卡 | `dispatch-plan-card.tsx` |
| 产物预览 / 产物库 | `artifact-preview-panel.tsx` · `artifact-library.tsx` |
| fs_write 审批面板 + diff | `pending-writes-panel.tsx` · `pending-write-diff-tab.tsx` |
| ask_user 结构化弹窗 | `ask-user-question-dialog.tsx` |
| Token 计量 | `usage-dashboard.tsx` · `usage-badge.tsx` |
| 文件浏览器 | `file-explorer-panel.tsx` · `file-tab.tsx` · `file-library-dialog.tsx` |
| 选区改写 / 引用 | `selection-popover.tsx` · `quoted-message.tsx` |
| 导航辅助 | `pinned-messages-bar.tsx` · `conversation-outline.tsx` |
| Agent 库 / 创建 | `agent-library.tsx` · `create-agent-dialog.tsx` · `add-agent-dialog.tsx` · `agent-avatar.tsx` · `agent-info-popover.tsx` |
| 会话创建 / 目录选择 | `new-conversation-dialog.tsx` · `dir-picker-dialog.tsx` |
| 设置面板 | `settings-dialog.tsx` |
| 渲染基建 | `markdown.tsx` · `code-block.tsx` · `attachment-chip.tsx` · `ui/*`（shadcn） |

### FastAPI 后端（`backend/app/`）

Next.js 通过 `next.config.ts` 将 API 和部署预览请求转发给 FastAPI。

| 文件 | 职责 |
|---|---|
| `routers/conversations.py` · `routers/pending.py` | 群聊消息和委派审批 |
| `routers/stream.py` · `services/event_bus.py` | SSE 全局连接和事件重放 |
| `runner/agent_runner.py` | AgentRun 生命周期、上下文注入、adapter 调用和消息持久化 |
| `runner/orchestration_graph.py` | LangGraph 单项审批与执行 |
| `services/pending_stores.py` | 进程内审批状态 |
| `services/conversation_service.py` · `services/conversation_context.py` | 会话持久化和历史注入 |
| `services/artifact_service.py` · `services/deployment_service.py` · `services/ppt_export.py` | 产物、部署和 PPT 导出 |
| `adapters/registry.py` · `adapters/types.py` | 适配器选择和事件流接口 |
| `tools/registry.py` · `services/workspace_utils.py` · `services/security.py` | 工具执行和 workspace 安全策略 |
| `db/client.py` · `db/bootstrap.py` · `db/builtin_agents.py` | SQLite 连接、建表和内置 Agent |

DB 文件：`.agenthub-data/agenthub.db`;workspace：`.agenthub-data/workspaces/<conv_xxx>/`（sandbox 模式）。

### 共享类型（`src/shared/`）
`types.ts`（**`StreamEvent` / `MessagePart` 等跨层类型,改动牵一发动全身**） · `constants.ts` · `model-registry.ts`（模型清单） · `ppt-theme.ts`（`PptTheme` 解析 + 专业默认,预览/导出共用）。

### 桌面（`electron/`）& 移动（`apps/mobile/`）
- Electron：`main.ts`（主进程） · `paths.ts`（userData 路径迁移） · `server-bootstrap.ts`（拉起 Next standalone）。`specs/12`。
- 移动：`apps/mobile/`（Capacitor 伴随客户端,monorepo workspace `@agenthub/mobile`）。`specs/14`。

### 测试（`e2e/` + `*.test.ts`）
- 单元：`src/**/*.test.ts`（Vitest 纯函数：security / workspace-utils / dispatch-plan / artifact-content / ppt-export / ppt-theme）。
- E2E：`e2e/`（Playwright；`global-setup.ts` 建隔离库 + 插 mock agent，`chat.spec.ts` / `conversations.spec.ts` 跑核心 IM 流）；配置 `playwright.config.ts`，命令 `pnpm e2e`。

---

## 附 · 当前现状（易过时,以 git 为准）

### ✅ 近期完成（最新一批）
- 群聊由主 Agent 接收消息并逐项委派，子 Run 详情随任务卡展示。
- 会话归档（service / API / sidebar，`archived` 字段早有，本批接通 UI）
- **PPT 产物**：`ppt` 类型 + 结构化 slides JSON + 真 .pptx 导出（pptxgenjs）+ 完整 theme token（预览/导出同源消费 `resolvePptTheme`）
- **Playwright E2E** 基建 + 核心 IM 流（mock agent；本机 `pnpm e2e` 跑，详见「测试」节）
- 斜杠命令菜单（`/` 浮层）

### 📋 待办
- E2E 第二批：产物预览/导出 + 群聊调度（需「会产 artifact / dispatch」的测试假 adapter）
- PPT 深化：辅色语义着色（正面↑ / 警示↓）+ 数据页卡片化
- Codex 写盘审批 hook（当前 Review 模式用 read-only sandbox）
- sandbox 配额对 Claude Code SDK 失效（SDK 自己写盘绕过 quota）
- 移动端伴随 App 配对通信打通

### ⚠️ 关键约定（动手前必看）
- 改实体字段时同步 `specs/01`。改事件时同步 `specs/02`。改 Bash 黑名单时同步 `specs/11` 和 `backend/app/services/security.py`。
- 所有 LLM 调用必带 `AbortSignal`;跨进程输入（API body / LLM 输出）必经 zod 校验;fs/bash 必过 Workspace 沙箱。
- 完整协作规则见 `CLAUDE.md`。

---

*最后更新于 2026-11-25，代码地图已同步 FastAPI 和 LangGraph 编排。*
