'use client'

import { Check, Copy } from 'lucide-react'
import { memo, useEffect, useRef, useState } from 'react'

import { getCachedHighlight, normalizeLang, scheduleHighlight } from '@/lib/highlighter'
import { cn } from '@/lib/utils'

interface CodeBlockProps {
  code: string
  language: string
  className?: string
  /** 流式输出中：暂缓 Shiki 高亮，避免每个 token 都触发全量高亮与 innerHTML 重解析。 */
  streaming?: boolean
}

const STREAMING_HIGHLIGHT_DEBOUNCE_MS = 350

// 全局复用单个 IntersectionObserver，视口外 400px 内的代码块才触发高亮
const visibilityCallbacks = new WeakMap<Element, () => void>()
let sharedObserver: IntersectionObserver | null = null

function observeNearViewport(el: Element, onVisible: () => void): () => void {
  if (typeof IntersectionObserver === 'undefined') {
    onVisible()
    return () => {}
  }
  if (!sharedObserver) {
    sharedObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          const cb = visibilityCallbacks.get(entry.target)
          if (cb) {
            visibilityCallbacks.delete(entry.target)
            sharedObserver?.unobserve(entry.target)
            cb()
          }
        }
      },
      { rootMargin: '400px 0px' },
    )
  }
  visibilityCallbacks.set(el, onVisible)
  sharedObserver.observe(el)
  return () => {
    visibilityCallbacks.delete(el)
    sharedObserver?.unobserve(el)
  }
}

/**
 * Shiki 双主题代码块。首帧先 fallback 纯 pre，靠近视口后再异步高亮；
 * 流式期间防抖等待代码稳定，防止每个 delta 反复触发主线程高亮卡顿。
 */
function CodeBlockImpl({ code, language, className, streaming = false }: CodeBlockProps) {
  const displayLang = normalizeLang(language)
  const langLabel = displayLang === 'text' ? language || 'text' : displayLang
  const renderKey = `${displayLang}\0${code}`

  const [highlighted, setHighlighted] = useState<{ key: string; html: string } | null>(() => {
    const cached = getCachedHighlight(code, language)
    return cached ? { key: renderKey, html: cached } : null
  })
  const [visible, setVisible] = useState(streaming)
  const [copied, setCopied] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const cachedSync = highlighted?.key === renderKey ? highlighted.html : getCachedHighlight(code, language)

  useEffect(() => {
    if (visible || cachedSync !== null) return
    const el = containerRef.current
    if (!el) return
    return observeNearViewport(el, () => setVisible(true))
  }, [visible, cachedSync])

  useEffect(() => {
    if (cachedSync !== null) return
    if (!visible && !streaming) return
    const rawLang = language.toLowerCase().trim()
    if (!rawLang || rawLang === 'text' || rawLang === 'plaintext' || rawLang === 'txt') return

    let cancelJob: (() => void) | null = null
    let timer: ReturnType<typeof setTimeout> | null = null

    const start = () => {
      cancelJob = scheduleHighlight(code, language, (out) => {
        setHighlighted({ key: renderKey, html: out })
      })
    }

    if (streaming) {
      timer = setTimeout(start, STREAMING_HIGHLIGHT_DEBOUNCE_MS)
    } else {
      start()
    }

    return () => {
      if (timer !== null) clearTimeout(timer)
      cancelJob?.()
    }
  }, [code, language, displayLang, renderKey, streaming, visible, cachedSync])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // ignore
    }
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        'group relative my-2 overflow-hidden rounded-md border',
        'border-zinc-200 bg-[#f6f8fa] text-zinc-900',
        'dark:border-zinc-800 dark:bg-[#0d1117] dark:text-zinc-100',
        className,
      )}
    >
      <div
        className={cn(
          'flex items-center justify-between border-b px-3 py-1.5 text-xs',
          'border-zinc-200 dark:border-zinc-800',
        )}
      >
        <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
          {langLabel}
        </span>
        <button
          type="button"
          onClick={copy}
          className={cn(
            'flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] opacity-0 transition group-hover:opacity-100',
            'text-zinc-500 hover:bg-zinc-200 hover:text-zinc-900',
            'dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100',
          )}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      {cachedSync ? (
        <div
          className="shiki-host overflow-x-auto px-3 py-2 text-xs leading-relaxed [&_pre]:!bg-transparent [&_pre]:!m-0 [&_pre]:!p-0 [&_code]:font-mono"
          dangerouslySetInnerHTML={{ __html: cachedSync }}
        />
      ) : (
        <pre className="overflow-x-auto px-3 py-2 font-mono text-xs leading-relaxed">
          <code>{code}</code>
        </pre>
      )}
    </div>
  )
}

export const CodeBlock = memo(CodeBlockImpl)
CodeBlock.displayName = 'CodeBlock'
