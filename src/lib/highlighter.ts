import type { HighlighterCore } from 'shiki/core'
import type { BundledLanguage } from 'shiki/langs'

// 用户输入的语言别名 → Shiki lang id（纯 JS 映射，不静态引入 shiki）
const LANG_ALIAS: Record<string, string> = {
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  py: 'python',
  rs: 'rust',
  sh: 'bash',
  zsh: 'bash',
  shell: 'bash',
  yml: 'yaml',
  md: 'markdown',
  'c++': 'cpp',
  cxx: 'cpp',
  cc: 'cpp',
  hpp: 'cpp',
  h: 'c',
  cs: 'csharp',
  'objective-c': 'objc',
  rb: 'ruby',
  kt: 'kotlin',
  gql: 'graphql',
  htm: 'html',
  ps1: 'powershell',
  dockerfile: 'docker',
}

// 常见已知语言集合，用于同步 normalizeLang（避免顶层 import shiki/langs 拖入首屏 bundle）
const KNOWN_LANGS = new Set<string>([
  'typescript',
  'tsx',
  'javascript',
  'jsx',
  'python',
  'json',
  'jsonc',
  'json5',
  'bash',
  'shellscript',
  'powershell',
  'markdown',
  'mdx',
  'html',
  'xml',
  'css',
  'scss',
  'sass',
  'less',
  'sql',
  'yaml',
  'toml',
  'ini',
  'diff',
  'docker',
  'dockerfile',
  'go',
  'rust',
  'java',
  'kotlin',
  'c',
  'cpp',
  'csharp',
  'objc',
  'php',
  'ruby',
  'swift',
  'dart',
  'lua',
  'r',
  'perl',
  'scala',
  'elixir',
  'erlang',
  'haskell',
  'clojure',
  'zig',
  'vue',
  'svelte',
  'astro',
  'graphql',
  'prisma',
  'protobuf',
  'nginx',
  'makefile',
  'cmake',
  'latex',
  'mermaid',
  'regex',
  'csv',
  'http',
  'dotenv',
])

const MAX_CACHE_ENTRIES = 200
const MAX_HIGHLIGHT_CHARS = 50_000

const htmlCache = new Map<string, string>()
const loadedLangs = new Set<string>()
const langLoadPromises = new Map<string, Promise<boolean>>()

let runtimePromise: Promise<{
  highlighter: HighlighterCore
  bundledLanguages: Record<string, () => Promise<unknown>>
}> | null = null

let workerInstance: Worker | null = null
let workerBroken = false
let nextJobId = 1
const pendingWorkerJobs = new Map<
  number,
  { code: string; lang: string; resolve: (html: string) => void }
>()

interface QueuedHighlight {
  code: string
  lang: string
  cancelled: boolean
  onDone: (html: string) => void
}

const highlightQueue: QueuedHighlight[] = []
let queueScheduled = false

function cacheKey(code: string, lang: string): string {
  return `${normalizeLang(lang)}\0${code}`
}

function getFromCache(key: string): string | null {
  const hit = htmlCache.get(key)
  if (hit === undefined) return null
  htmlCache.delete(key)
  htmlCache.set(key, hit)
  return hit
}

function putInCache(key: string, html: string): void {
  if (htmlCache.has(key)) htmlCache.delete(key)
  htmlCache.set(key, html)
  if (htmlCache.size > MAX_CACHE_ENTRIES) {
    const oldest = htmlCache.keys().next().value
    if (oldest !== undefined) htmlCache.delete(oldest)
  }
}

function getWorker(): Worker | null {
  if (workerBroken || typeof window === 'undefined' || typeof Worker === 'undefined') {
    return null
  }
  if (!workerInstance) {
    try {
      const worker = new Worker(new URL('./highlighter.worker.ts', import.meta.url), {
        type: 'module',
      })
      worker.onmessage = (event: MessageEvent<{ id: number; html: string }>) => {
        const pending = pendingWorkerJobs.get(event.data.id)
        if (!pending) return
        pendingWorkerJobs.delete(event.data.id)
        pending.resolve(event.data.html)
      }
      worker.onerror = () => {
        workerBroken = true
        worker.terminate()
        workerInstance = null
        // 降级到主线程补跑未完成的请求，不丢失高亮结果
        const inflight = Array.from(pendingWorkerJobs.values())
        pendingWorkerJobs.clear()
        for (const job of inflight) {
          void highlightInMainThread(job.code, job.lang).then(job.resolve)
        }
      }
      workerInstance = worker
    } catch {
      workerBroken = true
      return null
    }
  }
  return workerInstance
}

function highlightInWorker(worker: Worker, code: string, candidate: string): Promise<string> {
  return new Promise((resolve) => {
    const id = nextJobId++
    pendingWorkerJobs.set(id, { code, lang: candidate, resolve })
    worker.postMessage({ id, code, lang: candidate })
  })
}

async function getRuntime() {
  if (!runtimePromise) {
    runtimePromise = (async () => {
      const [{ createHighlighterCore }, { createOnigurumaEngine }, { bundledThemes }, { bundledLanguages }] =
        await Promise.all([
          import('shiki/core'),
          import('shiki/engine/oniguruma'),
          import('shiki/themes'),
          import('shiki/langs'),
        ])
      const highlighter = await createHighlighterCore({
        themes: [bundledThemes['github-light'], bundledThemes['github-dark']],
        langs: [],
        engine: createOnigurumaEngine(() => import('shiki/wasm')),
      })
      return {
        highlighter,
        bundledLanguages: bundledLanguages as Record<string, () => Promise<unknown>>,
      }
    })().catch((err) => {
      runtimePromise = null
      throw err
    })
  }
  return runtimePromise
}

function resolveKnownLang(raw: string | null | undefined): string | null {
  if (!raw) return null
  const k = raw.toLowerCase().trim()
  if (!k || k === 'text' || k === 'plaintext' || k === 'txt') return null
  if (k in LANG_ALIAS) return LANG_ALIAS[k]
  if (KNOWN_LANGS.has(k)) return k
  return null
}

export function normalizeLang(raw: string | null | undefined): string {
  return resolveKnownLang(raw) ?? 'text'
}

/** 同步查缓存；命中时 CodeBlock 首帧直接展示高亮结果，无闪烁。 */
export function getCachedHighlight(code: string, lang: string): string | null {
  if (!code || code.length > MAX_HIGHLIGHT_CHARS) return null
  return getFromCache(cacheKey(code, lang))
}

async function ensureLanguageLoaded(
  highlighter: HighlighterCore,
  bundledLanguages: Record<string, () => Promise<unknown>>,
  candidate: string,
): Promise<boolean> {
  if (loadedLangs.has(candidate)) return true
  const loader = bundledLanguages[candidate]
  if (!loader) return false

  let pending = langLoadPromises.get(candidate)
  if (!pending) {
    pending = highlighter
      .loadLanguage(loader as Parameters<HighlighterCore['loadLanguage']>[0])
      .then(() => {
        loadedLangs.add(candidate)
        return true
      })
      .catch(() => false)
      .finally(() => {
        langLoadPromises.delete(candidate)
      })
    langLoadPromises.set(candidate, pending)
  }
  return pending
}

async function highlightInMainThread(code: string, candidate: string): Promise<string> {
  try {
    const { highlighter, bundledLanguages } = await getRuntime()
    const ok = await ensureLanguageLoaded(highlighter, bundledLanguages, candidate)
    if (!ok) return wrapPlain(code)

    return highlighter.codeToHtml(code, {
      lang: candidate as BundledLanguage,
      themes: { light: 'github-light', dark: 'github-dark' },
    })
  } catch {
    return wrapPlain(code)
  }
}

/** 渲染成双主题 HTML：浏览器端走 Web Worker 离线分词（零主线程阻塞），Node/降级走主线程。 */
export async function highlightToHtml(code: string, lang: string): Promise<string> {
  if (!code) return wrapPlain('')
  if (code.length > MAX_HIGHLIGHT_CHARS) return wrapPlain(code)

  const key = cacheKey(code, lang)
  const cached = getFromCache(key)
  if (cached !== null) return cached

  const raw = lang?.toLowerCase().trim() ?? ''
  if (!raw || raw === 'text' || raw === 'plaintext' || raw === 'txt') {
    return wrapPlain(code)
  }
  const candidate = LANG_ALIAS[raw] ?? raw

  const worker = getWorker()
  const html = worker
    ? await highlightInWorker(worker, code, candidate)
    : await highlightInMainThread(code, candidate)

  putInCache(key, html)
  return html
}

function flushNextHighlight(): void {
  queueScheduled = false
  while (highlightQueue.length > 0) {
    const job = highlightQueue.shift()!
    if (job.cancelled) continue

    void highlightToHtml(job.code, job.lang).then((html) => {
      if (!job.cancelled) job.onDone(html)
      if (highlightQueue.length > 0) scheduleQueuePump()
    })
    return
  }
}

function scheduleQueuePump(): void {
  if (queueScheduled) return
  queueScheduled = true
  setTimeout(flushNextHighlight, 0)
}

/** 协作式排队高亮，支持取消（组件卸载或流式更新时直接跳过）。 */
export function scheduleHighlight(
  code: string,
  lang: string,
  onDone: (html: string) => void,
): () => void {
  const cached = getCachedHighlight(code, lang)
  if (cached !== null) {
    onDone(cached)
    return () => {}
  }

  const job: QueuedHighlight = { code, lang, cancelled: false, onDone }
  highlightQueue.push(job)
  scheduleQueuePump()

  return () => {
    job.cancelled = true
  }
}

function wrapPlain(code: string): string {
  const escaped = code
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return `<pre><code>${escaped}</code></pre>`
}
