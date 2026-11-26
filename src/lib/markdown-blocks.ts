const LINK_DEF_OR_FOOTNOTE_RE = /(?:^|\n)\[[^\]]+\]:\s/

/**
 * 流式输出期间将 Markdown 按顶层空行切分成「已稳定前缀块 + 活跃尾块」，
 * 让前面已完成的段落 / 代码块被 React.memo 冻住，避免每次 delta 全量重解析。
 *
 * 含 reference link / footnote 定义时退回单块，保证 GFM 语义完整。
 */
export function splitMarkdownForStreaming(markdown: string): string[] {
  if (!markdown || markdown.length < 200 || !markdown.includes('\n\n') || LINK_DEF_OR_FOOTNOTE_RE.test(markdown)) {
    return [markdown]
  }

  const lines = markdown.split('\n')
  const blocks: string[] = []
  let current: string[] = []
  let fenceChar: '`' | '~' | null = null
  let fenceLen = 0
  let inTable = false

  const flush = () => {
    if (current.length === 0) return
    const joined = current.join('\n')
    if (joined.trim().length > 0) blocks.push(joined)
    current = []
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trimStart()

    if (fenceChar === null) {
      const open = /^(```+|~~~+)/.exec(trimmed)
      if (open) {
        fenceChar = open[1][0] as '`' | '~'
        fenceLen = open[1].length
        inTable = false
        current.push(line)
        continue
      }
    } else {
      const closeRe = fenceChar === '`' ? /^`+\s*$/ : /^~+\s*$/
      if (closeRe.test(trimmed) && trimmed.trimEnd().length >= fenceLen) {
        fenceChar = null
        fenceLen = 0
      }
      current.push(line)
      continue
    }

    if (trimmed === '') {
      inTable = false
      const nextLine = lines[i + 1]
      // 不在列表 / 引用 / 缩进续行之间切分，避免拆断松散列表或嵌套块
      const nextTrimmed = nextLine?.trimStart() ?? ''
      const continuesStructure =
        nextLine !== undefined &&
        (nextLine.startsWith('  ') ||
          nextLine.startsWith('\t') ||
          /^[-*+]\s/.test(nextTrimmed) ||
          /^\d+[.)]\s/.test(nextTrimmed) ||
          nextTrimmed.startsWith('>'))
      if (!continuesStructure && current.length > 0) {
        flush()
      } else {
        current.push(line)
      }
      continue
    }

    if (trimmed.startsWith('|')) {
      inTable = true
    } else if (inTable) {
      inTable = false
    }

    current.push(line)
  }

  flush()
  return blocks.length > 0 ? blocks : [markdown]
}
