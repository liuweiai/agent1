import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const code = await readFile(new URL('../src/runEvents.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
const { subscribeRunEvents, cursorFromEvents } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

function harness(overrides = {}) {
  const values = new Map()
  const source = { listeners: new Map(), closed: false,
    addEventListener(name, fn) { this.listeners.set(name, fn) },
    close() { this.closed = true },
    emit(name, id = '') { this.listeners.get(name)?.({ lastEventId: String(id) }) },
  }
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) }
  let url; let refreshes = 0; let done = 0
  const subscription = subscribeRunEvents('abc', {
    url: after => `/api/runs/abc/events?after=${after}`, storage,
    refresh: async () => { refreshes++ }, onDone: () => { done++ },
    createSource: value => { url = value; return source }, ...overrides,
  })
  return { source, storage, values, subscription, get url() { return url }, get refreshes() { return refreshes }, get done() { return done } }
}

test('stores processed cursor per run and reuses it on a new connection', async () => {
  const first = harness({ initialCursor: 12 })
  assert.equal(first.url, '/api/runs/abc/events?after=12')
  first.source.emit('timeline', 15)
  await tick()
  assert.equal(first.values.get('factorypilot:sse:v1:abc'), '15')
  first.subscription.close()
  const second = harness({ storage: first.storage })
  assert.equal(second.url, '/api/runs/abc/events?after=15')
  second.source.emit('timeline', 15)
  second.source.emit('timeline', 14)
  await tick()
  assert.equal(second.refreshes, 0)
  second.subscription.close()
})

test('serializes and coalesces bursts so stale GET responses cannot overwrite new state', async () => {
  const firstGet = deferred()
  let calls = 0
  const h = harness({ refresh: () => ++calls === 1 ? firstGet.promise : Promise.resolve() })
  h.source.emit('timeline', 1)
  h.source.emit('timeline', 2)
  h.source.emit('timeline', 3)
  assert.equal(calls, 1)
  assert.equal(h.values.get('factorypilot:sse:v1:abc'), '0')
  firstGet.resolve()
  await tick()
  assert.equal(calls, 2)
  assert.equal(h.values.get('factorypilot:sse:v1:abc'), '3')
  h.subscription.close()
})

test('failed refresh does not commit cursor and is retried even after done', async () => {
  const firstGet = deferred()
  let calls = 0
  const h = harness({ retryMs: 5, refresh: () => ++calls === 1 ? firstGet.promise : Promise.resolve() })
  h.source.emit('timeline', 8)
  h.source.emit('done')
  firstGet.reject(new Error('temporary network error'))
  await tick()
  assert.equal(h.values.get('factorypilot:sse:v1:abc'), '0')
  await new Promise(resolve => setTimeout(resolve, 30))
  assert.equal(h.values.get('factorypilot:sse:v1:abc'), '8')
  assert.equal(h.done, 1)
  assert.equal(h.source.closed, true)
  h.subscription.close()
})

test('done during an earlier GET forces a final refresh after terminal state', async () => {
  const firstGet = deferred()
  let calls = 0
  const h = harness({ refresh: () => ++calls === 1 ? firstGet.promise : Promise.resolve() })
  h.source.emit('timeline', 7)
  h.source.emit('done')
  firstGet.resolve()
  await tick()
  assert.equal(calls, 2)
  assert.equal(h.done, 1)
})

test('closing a selected run cancels pending retries and late cursor updates', async () => {
  const pending = deferred()
  const h = harness({ refresh: () => pending.promise })
  h.source.emit('timeline', 9)
  h.subscription.close()
  pending.resolve()
  await tick()
  assert.equal(h.values.get('factorypilot:sse:v1:abc'), '0')
  h.source.emit('timeline', 10)
  assert.equal(h.source.closed, true)
})

test('invalid IDs and unavailable storage do not break live updates', async () => {
  const h = harness({ storage: { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } } })
  for (const value of ['bad', '-1', '1.2', '9007199254740992']) h.source.emit('timeline', value)
  h.source.emit('timeline', 4)
  await tick()
  assert.equal(h.refreshes, 1)
  assert.equal(cursorFromEvents([{ id: 2 }, { id: 9 }, { id: 5 }]), 9)
  h.subscription.close()
})
