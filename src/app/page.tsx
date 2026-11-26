import { ChatPanel } from '@/components/chat-panel'
import { MessageHighlightLayer } from '@/components/message-highlight-layer'
import { RightPanels } from '@/components/right-panels'
import { SelectionPopover } from '@/components/selection-popover'
import { Sidebar } from '@/components/sidebar'

export default function Home() {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <ChatPanel />
      <RightPanels />
      <SelectionPopover />
      <MessageHighlightLayer />
    </div>
  )
}
