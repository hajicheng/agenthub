import { describe, expect, it } from 'vitest'

import { getCachedHighlight, highlightToHtml, normalizeLang, scheduleHighlight } from './highlighter'

describe('highlighter', () => {
  it('normalizes language aliases without loading shiki', () => {
    expect(normalizeLang('ts')).toBe('typescript')
    expect(normalizeLang('py')).toBe('python')
    expect(normalizeLang('sh')).toBe('bash')
    expect(normalizeLang('c++')).toBe('cpp')
    expect(normalizeLang('unknown-lang-xyz')).toBe('text')
    expect(normalizeLang('')).toBe('text')
  })

  it('highlights code with dual themes and populates synchronous cache', async () => {
    const snippet = 'export const perfScore: number = 100'
    const html = await highlightToHtml(snippet, 'ts')
    expect(html).toContain('class="shiki')
    expect(html).toContain('--shiki-dark')
    expect(getCachedHighlight(snippet, 'typescript')).toBe(html)
  })

  it('skips cancelled scheduled highlights', async () => {
    let called = false
    const cancel = scheduleHighlight('const cancelledTest = 1', 'javascript', () => {
      called = true
    })
    cancel()
    await new Promise((r) => setTimeout(r, 25))
    expect(called).toBe(false)
  })
})
