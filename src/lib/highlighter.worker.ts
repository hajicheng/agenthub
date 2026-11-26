import type { HighlighterCore } from 'shiki/core'
import type { BundledLanguage } from 'shiki/langs'

interface HighlightRequest {
  id: number
  code: string
  lang: string
}

interface HighlightResponse {
  id: number
  html: string
}

const loadedLangs = new Set<string>()
const langLoadPromises = new Map<string, Promise<boolean>>()

let runtimePromise: Promise<{
  highlighter: HighlighterCore
  bundledLanguages: Record<string, () => Promise<unknown>>
}> | null = null

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

async function ensureLanguageLoaded(
  highlighter: HighlighterCore,
  bundledLanguages: Record<string, () => Promise<unknown>>,
  lang: string,
): Promise<boolean> {
  if (loadedLangs.has(lang)) return true
  const loader = bundledLanguages[lang]
  if (!loader) return false

  let pending = langLoadPromises.get(lang)
  if (!pending) {
    pending = highlighter
      .loadLanguage(loader as Parameters<HighlighterCore['loadLanguage']>[0])
      .then(() => {
        loadedLangs.add(lang)
        return true
      })
      .catch(() => false)
      .finally(() => {
        langLoadPromises.delete(lang)
      })
    langLoadPromises.set(lang, pending)
  }
  return pending
}

function wrapPlain(code: string): string {
  const escaped = code
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return `<pre><code>${escaped}</code></pre>`
}

self.addEventListener('message', (event: MessageEvent<HighlightRequest>) => {
  const { id, code, lang } = event.data
  void (async () => {
    let html: string
    try {
      const { highlighter, bundledLanguages } = await getRuntime()
      const ok = await ensureLanguageLoaded(highlighter, bundledLanguages, lang)
      html = ok
        ? highlighter.codeToHtml(code, {
            lang: lang as BundledLanguage,
            themes: { light: 'github-light', dark: 'github-dark' },
          })
        : wrapPlain(code)
    } catch {
      html = wrapPlain(code)
    }
    const response: HighlightResponse = { id, html }
    self.postMessage(response)
  })()
})
