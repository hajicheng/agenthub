'use client'

import { isValidElement, memo, useMemo, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { CodeBlock } from '@/components/code-block'
import { splitMarkdownForStreaming } from '@/lib/markdown-blocks'
import { cn } from '@/lib/utils'

interface MarkdownProps {
  children: string
  className?: string
  streaming?: boolean
}

const REMARK_PLUGINS = [remarkGfm]

function createMarkdownComponents(streaming: boolean): Components {
  return {
    h1: ({ children }) => <h1 className="mt-2 mb-1 text-lg font-semibold">{children}</h1>,
    h2: ({ children }) => <h2 className="mt-2 mb-1 text-base font-semibold">{children}</h2>,
    h3: ({ children }) => <h3 className="mt-2 mb-1 text-sm font-semibold">{children}</h3>,
    h4: ({ children }) => <h4 className="mt-2 mb-1 text-sm font-semibold">{children}</h4>,
    p: ({ children }) => <p className="my-1.5 leading-6">{children}</p>,
    ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{children}</ol>,
    li: ({ children }) => <li className="leading-6">{children}</li>,
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline-offset-2 hover:underline"
      >
        {children}
      </a>
    ),
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    blockquote: ({ children }) => (
      <blockquote className="my-1.5 border-l-2 border-muted-foreground/30 pl-3 italic text-muted-foreground">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-3 border-muted" />,
    code: ({ className: codeClass, children }) => {
      const isBlock = codeClass?.startsWith('language-')
      if (isBlock) {
        const language = codeClass!.slice('language-'.length)
        return (
          <CodeBlock
            code={String(children).replace(/\n$/, '')}
            language={language}
            streaming={streaming}
          />
        )
      }
      return (
        <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px]">
          {children}
        </code>
      )
    },
    pre: ({ children }) => {
      // children 已经是 <CodeBlock />，pre 只透传，避免再套一层 <pre>。
      if (isCodeBlockChild(children)) return <>{children}</>
      return (
        <pre className="my-2 overflow-x-auto rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs leading-relaxed text-zinc-900 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100">
          {children}
        </pre>
      )
    },
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto">
        <table className="w-full border-collapse text-xs">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border-b border-foreground/20 px-2 py-1 text-left font-semibold">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="border-b border-foreground/10 px-2 py-1 align-top">{children}</td>
    ),
  }
}

const STATIC_COMPONENTS = createMarkdownComponents(false)
const STREAMING_COMPONENTS = createMarkdownComponents(true)

const MarkdownBlock = memo(function MarkdownBlock({
  content,
  streaming,
}: {
  content: string
  streaming: boolean
}) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      components={streaming ? STREAMING_COMPONENTS : STATIC_COMPONENTS}
    >
      {content}
    </ReactMarkdown>
  )
})

/**
 * 受控的 Markdown 渲染器。fenced code block 交给 CodeBlock（shiki 双主题高亮），
 * 流式输出时按稳定块切分并 memo 冻结前置段落，避免每次 delta 全量重解析。
 */
function MarkdownImpl({ children, className, streaming = false }: MarkdownProps) {
  const blocks = useMemo(
    () => (streaming ? splitMarkdownForStreaming(children) : null),
    [children, streaming],
  )

  return (
    <div className={cn('text-sm leading-6 text-foreground', className)}>
      {blocks && blocks.length > 1 ? (
        blocks.map((block, index) => (
          <MarkdownBlock
            key={index}
            content={block}
            streaming={index === blocks.length - 1}
          />
        ))
      ) : (
        <MarkdownBlock content={children} streaming={streaming} />
      )}
    </div>
  )
}

export const Markdown = memo(MarkdownImpl)
Markdown.displayName = 'Markdown'

function isCodeBlockChild(node: ReactNode): boolean {
  if (Array.isArray(node)) return node.some(isCodeBlockChild)
  if (!isValidElement(node)) return false
  const el = node as ReactElement<{ className?: string }>
  return el.type === CodeBlock || el.props?.className?.startsWith('language-') === true
}
