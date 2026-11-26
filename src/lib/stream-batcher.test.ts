import { describe, expect, it } from 'vitest'

import type { StreamEvent } from '@/shared/types'

import { coalesceStreamEvents, createStreamEventBatcher } from './stream-batcher'

const BASE = { conversationId: 'conv_1', timestamp: 1 }

function textDelta(text: string, partIndex = 0, timestamp = 1): StreamEvent {
  return {
    ...BASE,
    timestamp,
    type: 'part.delta',
    messageId: 'msg_1',
    partIndex,
    delta: { type: 'text.append', text },
  }
}

function thinkingDelta(text: string, partIndex = 0, timestamp = 1): StreamEvent {
  return {
    ...BASE,
    timestamp,
    type: 'part.delta',
    messageId: 'msg_1',
    partIndex,
    delta: { type: 'thinking.append', text },
  }
}

describe('coalesceStreamEvents', () => {
  it('merges consecutive deltas for the same part and drops heartbeats', () => {
    const events: StreamEvent[] = [
      { ...BASE, type: 'heartbeat' },
      textDelta('Hello', 0, 10),
      textDelta(' ', 0, 11),
      textDelta('world', 0, 12),
    ]
    const merged = coalesceStreamEvents(events)
    expect(merged).toEqual([textDelta('Hello world', 0, 12)])
  })

  it('preserves boundaries across different parts, delta types, and non-delta events', () => {
    const endEvent: StreamEvent = { ...BASE, timestamp: 20, type: 'message.end', messageId: 'msg_1' }
    const events: StreamEvent[] = [
      thinkingDelta('a', 0, 1),
      thinkingDelta('b', 0, 2),
      textDelta('c', 1, 3),
      textDelta('d', 1, 4),
      endEvent,
    ]
    expect(coalesceStreamEvents(events)).toEqual([
      thinkingDelta('ab', 0, 2),
      textDelta('cd', 1, 4),
      endEvent,
    ])
  })
})

describe('createStreamEventBatcher', () => {
  it('batches rapid part.delta events into a single frame flush', () => {
    const flushed: StreamEvent[][] = []
    let scheduledCb: (() => void) | null = null
    const batcher = createStreamEventBatcher({
      onFlush: (batch) => flushed.push(batch),
      schedule: (cb) => {
        scheduledCb = cb
        return 1
      },
      cancel: () => {
        scheduledCb = null
      },
    })

    batcher.push(textDelta('a', 0, 1))
    batcher.push(textDelta('b', 0, 2))
    batcher.push(textDelta('c', 0, 3))
    expect(flushed).toHaveLength(0)

    scheduledCb!()
    expect(flushed).toEqual([[textDelta('abc', 0, 3)]])
  })

  it('flushes buffered deltas synchronously before applying non-delta events', () => {
    const flushed: StreamEvent[][] = []
    let cancelled = false
    const batcher = createStreamEventBatcher({
      onFlush: (batch) => flushed.push(batch),
      schedule: () => 42,
      cancel: () => {
        cancelled = true
      },
    })

    const endEvent: StreamEvent = { ...BASE, timestamp: 5, type: 'message.end', messageId: 'msg_1' }
    batcher.push(textDelta('hi', 0, 1))
    batcher.push(textDelta('!', 0, 2))
    batcher.push(endEvent)

    expect(cancelled).toBe(true)
    expect(flushed).toEqual([[textDelta('hi!', 0, 2), endEvent]])
  })

  it('drops buffered events on clear', () => {
    const flushed: StreamEvent[][] = []
    const batcher = createStreamEventBatcher({
      onFlush: (batch) => flushed.push(batch),
      schedule: () => 1,
      cancel: () => {},
    })

    batcher.push(textDelta('stale'))
    batcher.clear()
    batcher.flush()
    expect(flushed).toHaveLength(0)
  })
})
