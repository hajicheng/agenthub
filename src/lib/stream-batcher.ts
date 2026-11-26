import type { StreamEvent } from '@/shared/types'

type PartDeltaEvent = Extract<StreamEvent, { type: 'part.delta' }>

function canCoalesceDelta(prev: StreamEvent, next: StreamEvent): prev is PartDeltaEvent {
  return (
    prev.type === 'part.delta' &&
    next.type === 'part.delta' &&
    prev.conversationId === next.conversationId &&
    prev.messageId === next.messageId &&
    prev.partIndex === next.partIndex &&
    prev.delta.type === next.delta.type
  )
}

/** 合并同一 part 上连续的 part.delta，保留所有非 delta 事件的精确顺序。 */
export function coalesceStreamEvents(events: readonly StreamEvent[]): StreamEvent[] {
  const out: StreamEvent[] = []
  for (const event of events) {
    if (event.type === 'heartbeat') continue
    const prev = out[out.length - 1]
    if (prev && canCoalesceDelta(prev, event)) {
      out[out.length - 1] = {
        ...prev,
        timestamp: event.timestamp,
        delta: {
          type: prev.delta.type,
          text: prev.delta.text + (event as PartDeltaEvent).delta.text,
        },
      }
      continue
    }
    out.push(event)
  }
  return out
}

export interface StreamEventBatcher {
  push(event: StreamEvent): void
  flush(): void
  clear(): void
}

interface BatcherOptions {
  onFlush: (events: StreamEvent[]) => void
  schedule?: (cb: () => void) => number
  cancel?: (handle: number) => void
}

function defaultSchedule(cb: () => void): number {
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    if (typeof document !== 'undefined' && document.hidden) {
      return window.setTimeout(cb, 32)
    }
    return window.requestAnimationFrame(() => cb())
  }
  return Number(setTimeout(cb, 16))
}

function defaultCancel(handle: number): void {
  if (typeof window !== 'undefined' && typeof window.cancelAnimationFrame === 'function') {
    window.cancelAnimationFrame(handle)
    window.clearTimeout(handle)
    return
  }
  clearTimeout(handle)
}

/**
 * 按帧合并高频 part.delta（每帧最多一次 Zustand/React 更新）；
 * 非 delta 事件（start / end / tool / pending 等）立即与前置 delta 同批刷新，不破坏事件顺序。
 */
export function createStreamEventBatcher({
  onFlush,
  schedule = defaultSchedule,
  cancel = defaultCancel,
}: BatcherOptions): StreamEventBatcher {
  let queue: StreamEvent[] = []
  let scheduledHandle: number | null = null

  const cancelScheduled = () => {
    if (scheduledHandle !== null) {
      cancel(scheduledHandle)
      scheduledHandle = null
    }
  }

  const flush = () => {
    cancelScheduled()
    if (queue.length === 0) return
    const batch = queue
    queue = []
    onFlush(batch)
  }

  const push = (event: StreamEvent) => {
    if (event.type === 'heartbeat') return

    const prev = queue[queue.length - 1]
    if (prev && canCoalesceDelta(prev, event)) {
      queue[queue.length - 1] = {
        ...prev,
        timestamp: event.timestamp,
        delta: {
          type: prev.delta.type,
          text: prev.delta.text + (event as PartDeltaEvent).delta.text,
        },
      }
    } else {
      queue.push(event)
    }

    if (event.type !== 'part.delta') {
      flush()
      return
    }

    if (scheduledHandle === null) {
      scheduledHandle = schedule(() => {
        scheduledHandle = null
        flush()
      })
    }
  }

  const clear = () => {
    cancelScheduled()
    queue = []
  }

  return { push, flush, clear }
}
