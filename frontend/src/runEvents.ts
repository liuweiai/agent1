/** Resume notifications only after their corresponding snapshot has been applied. */
type CursorStorage = Pick<Storage, 'getItem' | 'setItem'>
type EventStream = Pick<EventSource, 'addEventListener' | 'close'>

interface RunEventOptions {
  url: (after: number) => string
  refresh: () => Promise<unknown>
  onDone: () => void
  initialCursor?: number
  storage?: CursorStorage
  createSource?: (url: string) => EventStream
  retryMs?: number
}

const cursorKey = (runId: string) => `factorypilot:sse:v1:${runId}`

function parseCursor(value: string | null): number {
  if (!value || !/^\d+$/.test(value)) return 0
  const result = Number(value)
  return Number.isSafeInteger(result) && result >= 0 ? result : 0
}

export function cursorFromEvents(events: Array<{ id: number }>): number {
  return events.reduce((last, event) => Number.isSafeInteger(event.id) ? Math.max(last, event.id) : last, 0)
}

export function subscribeRunEvents(runId: string, options: RunEventOptions): { close: () => void } {
  let storage = options.storage
  if (!storage) {
    try { storage = window.sessionStorage } catch { /* Private/restricted storage: memory only. */ }
  }
  let stored = 0
  try { stored = parseCursor(storage?.getItem(cursorKey(runId)) ?? null) } catch { /* Memory fallback. */ }
  let committed = Math.max(stored, options.initialCursor ?? 0)
  const persist = () => {
    try { storage?.setItem(cursorKey(runId), String(committed)) } catch { /* Memory fallback. */ }
  }
  persist()

  let pending = committed
  let inFlight = false
  let closed = false
  let done = false
  let doneRefreshed = false
  let retry: ReturnType<typeof setTimeout> | undefined
  const source = (options.createSource ?? ((url) => new EventSource(url, { withCredentials: true })))(options.url(committed))

  const flush = async () => {
    if (closed || inFlight || retry !== undefined) return
    inFlight = true
    try {
      while (!closed && (pending > committed || (done && !doneRefreshed))) {
        const target = pending
        const terminalRefresh = done
        await options.refresh()
        if (closed) return
        committed = Math.max(committed, target)
        persist()
        if (terminalRefresh) {
          doneRefreshed = true
          options.onDone()
          closed = true
        }
      }
    } catch {
      // Keep the cursor uncommitted on failed GET; retry even if the SSE stream ended.
      if (!closed) retry = setTimeout(() => { retry = undefined; void flush() }, options.retryMs ?? 1000)
    } finally {
      inFlight = false
    }
  }

  source.addEventListener('timeline', ((event: MessageEvent) => {
    if (closed) return
    const id = parseCursor(event.lastEventId)
    if (id <= pending) return
    pending = id
    void flush()
  }) as EventListener)
  source.addEventListener('done', () => {
    if (closed) return
    done = true
    source.close()
    void flush()
  })
  // Native EventSource reconnects with Last-Event-ID; gateway and Agent both consume it.
  return {
    close() {
      closed = true
      source.close()
      if (retry !== undefined) clearTimeout(retry)
    },
  }
}
