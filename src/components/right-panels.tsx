'use client'

import dynamic from 'next/dynamic'

import { useAppStore } from '@/stores/app-store'

const ArtifactPreviewPanel = dynamic(
  () => import('@/components/artifact-preview-panel').then((m) => m.ArtifactPreviewPanel),
  { ssr: false },
)

const FileExplorerPanel = dynamic(
  () => import('@/components/file-explorer-panel').then((m) => m.FileExplorerPanel),
  { ssr: false },
)

/**
 * 右侧面板按需加载壳：仅在真正打开文件树或产物预览时才拉取对应重型 chunk
 * （react-diff-viewer / mermaid / ppt / codemirror），不阻塞首屏 JS。
 */
export function RightPanels() {
  const fileExplorerOpen = useAppStore((s) => s.fileExplorerOpen)
  const previewArtifactId = useAppStore((s) => s.previewArtifactId)

  return (
    <>
      {fileExplorerOpen && <FileExplorerPanel />}
      {previewArtifactId && <ArtifactPreviewPanel />}
    </>
  )
}
