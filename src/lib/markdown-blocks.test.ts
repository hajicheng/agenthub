import { describe, expect, it } from 'vitest'

import { splitMarkdownForStreaming } from './markdown-blocks'

describe('splitMarkdownForStreaming', () => {
  it('keeps short markdown as a single block', () => {
    const text = 'Hello\n\nWorld'
    expect(splitMarkdownForStreaming(text)).toEqual([text])
  })

  it('splits completed paragraphs and fenced code blocks while keeping internal blank lines intact', () => {
    const intro = '### 架构说明\n\n' + '这是一段足够长的引导说明。'.repeat(20)
    const code = [
      '```javascript',
      'function a() {',
      '  return 1',
      '}',
      '',
      'function b() {',
      '  return 2',
      '}',
      '```',
    ].join('\n')
    const tail = '正在继续输出后续分析内容...'
    const markdown = `${intro}\n\n${code}\n\n${tail}`

    const blocks = splitMarkdownForStreaming(markdown)
    expect(blocks.length).toBeGreaterThanOrEqual(3)
    expect(blocks).toContain(code)
    expect(blocks[blocks.length - 1]).toBe(tail)
  })

  it('keeps an open streaming code fence intact as the tail block', () => {
    const intro = '这是一段前置说明文字。'.repeat(30)
    const openFence = '```ts\nconst x = 1\n\nconst y = 2\n'
    const blocks = splitMarkdownForStreaming(`${intro}\n\n${openFence}`)

    expect(blocks).toEqual([intro, openFence])
  })

  it('falls back to a single block when reference link definitions are present', () => {
    const text = `${'参考链接说明。'.repeat(40)}\n\n见 [docs][1]。\n\n[1]: https://example.com`
    expect(splitMarkdownForStreaming(text)).toEqual([text])
  })
})
