// Browser QA for the offline map, failure-report UI, and native EventSource reconnect.
// Set FACTORYPILOT_BROWSER_MODULES to a directory containing playwright and typescript.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile, mkdir } from 'node:fs/promises'
import { dirname, resolve, extname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const modules = process.env.FACTORYPILOT_BROWSER_MODULES
if (!modules) throw new Error('Set FACTORYPILOT_BROWSER_MODULES to the browser runtime node_modules directory')
const { chromium } = await import(pathToFileURL(resolve(modules, 'playwright/index.mjs')).href)
const { default: ts } = await import(pathToFileURL(resolve(root, 'frontend/node_modules/typescript/lib/typescript.js')).href)
const source = await readFile(resolve(root, 'frontend/src/runEvents.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
const output = resolve(root, '.test-tmp/execution-update-browser')
await mkdir(output, { recursive: true })
const connections = []
let snapshots = 0
const run = {
  id: 'verification-demo-run', query: '写入验证失败演示：抽检设置未生效', line_id: 'LINE-02', status: 'failed',
  severity: 'high', risk_level: 'high', created_at: '2026-09-22T08:00:00Z', updated_at: '2026-09-22T08:02:00Z',
  state: { verification: { mode: 'simulation', write_state_verified: false,
    baseline_yield: 91, verification_batches: [], release_condition_met: false,
    failure_reasons: ['抽检周期应为 10 分钟，读回为 30 分钟'],
    note: '写入读回验证失败：抽检周期应为 10 分钟，读回为 30 分钟。暂停质量放行判断，需人工核查。',
    next_steps: ['人工核对维修工单和抽检设置。修正原因后重新提交处置并审批。'],
  } },
  approval: { approved: true, approver: 'supervisor', comment: '批准模拟处置', decided_at: '2026-09-22T08:01:00Z' },
  events: [
    { id: 1, run_id: 'verification-demo-run', step: 'verify', title: '处理结果验证', status: 'failed', detail: '抽检设置不符', created_at: '2026-09-22T08:02:00Z', payload: {} },
    { id: 2, run_id: 'verification-demo-run', step: 'verification_failed', title: '写入验证失败，转人工核查', status: 'failed', detail: '已转人工核查', created_at: '2026-09-22T08:02:00Z', payload: {} },
  ],
  report: { report_id: 'IR-DEMO', outcome: 'failed', evidence_count: 8,
    conclusion: '写入读回验证失败，需人工核查；本次未确认质量放行。', data_notice: '本报告使用模拟 MES 数据生成。' },
}
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.md': 'text/plain' }
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1')
    const json = value => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
    if (url.pathname === '/probe/module.js') { res.setHeader('Content-Type', 'text/javascript'); return res.end(compiled) }
    if (url.pathname === '/probe/snapshot') { snapshots++; return json({ version: snapshots }) }
    if (url.pathname === '/probe/events') {
      connections.push({ after: url.searchParams.get('after'), last: req.headers['last-event-id'] ?? null })
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
      if (connections.length === 1) {
        res.write('retry: 150\nid: 41\nevent: timeline\ndata: {}\n\n')
        return setTimeout(() => res.end(), 100)
      }
      res.end('id: 42\nevent: timeline\ndata: {}\n\nevent: done\ndata: {}\n\n')
      return
    }
    if (url.pathname === '/probe') {
      res.setHeader('Content-Type', 'text/html')
      return res.end('<html><body><script type="module">import {subscribeRunEvents} from "/probe/module.js";window.probeDone=false;subscribeRunEvents("probe",{url:after=>"/probe/events?after="+after,refresh:async()=>{const r=await fetch("/probe/snapshot");if(!r.ok)throw Error("GET failed");await r.json()},onDone:()=>window.probeDone=true})</script></body></html>')
    }
    if (url.pathname === '/api/auth/me') return json({ username: 'supervisor', display_name: '刘工', roles: ['SUPERVISOR'] })
    if (url.pathname === '/api/runs') return json([run])
    if (url.pathname === '/api/runs/verification-demo-run') return json(run)
    const pathname = decodeURIComponent(url.pathname)
    const file = pathname.startsWith('/docs/') ? resolve(root, '.' + pathname) : resolve(root, 'frontend/dist', '.' + (pathname === '/' ? '/index.html' : pathname))
    if (!file.startsWith(root + '\\') && !file.startsWith(root + '/')) { res.writeHead(403); return res.end() }
    res.setHeader('Content-Type', mime[extname(file)] ?? 'application/octet-stream')
    res.end(await readFile(file))
  } catch { res.writeHead(404); res.end('Not found') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const base = `http://127.0.0.1:${server.address().port}`
let browser
try {
  browser = await chromium.launch({ executablePath: process.env.FACTORYPILOT_CHROME ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base + '/docs/' + encodeURIComponent('FactoryPilot执行链路思维导图.html'))
  assert.equal(await page.locator('.branch').count(), 12)
  await page.screenshot({ path: resolve(output, 'mindmap-reading.png') })
  await page.getByRole('button', { name: '全部折叠' }).click()
  assert.equal(await page.locator('.branch[open]').count(), 0)
  await page.getByRole('button', { name: '全部展开' }).click()
  await page.getByRole('searchbox').fill('verification_failed')
  assert.equal(await page.locator('.branch:not([hidden])').count(), 2)
  await page.getByRole('searchbox').fill('')
  await page.getByRole('button', { name: '总览导图', exact: true }).click()
  await page.getByRole('button', { name: '原始大小' }).click()
  assert.equal(await page.locator('#scale').textContent(), '100%')
  await page.getByRole('button', { name: '适应宽度' }).click()
  await page.screenshot({ path: resolve(output, 'mindmap-overview.png') })
  await page.getByRole('button', { name: '分支阅读', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
  await page.screenshot({ path: resolve(output, 'mindmap-mobile.png') })

  await page.setViewportSize({ width: 1440, height: 1050 })
  await page.goto(base + '/')
  await page.locator('.recent-run-item').click()
  await page.locator('.report-card').waitFor()
  assert.match(await page.locator('.report-card').innerText(), /写入验证失败，需人工核查/)
  await page.locator('.report-card').screenshot({ path: resolve(output, 'verification-failure-report.png') })

  await page.goto(base + '/probe')
  await page.waitForFunction(() => window.probeDone === true)
  assert.equal(connections.length, 2)
  assert.deepEqual(connections[0], { after: '0', last: null })
  assert.deepEqual(connections[1], { after: '0', last: '41' })
  assert.equal(await page.evaluate(() => sessionStorage.getItem('factorypilot:sse:v1:probe')), '42')
  assert.ok(snapshots >= 2)
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ checks: ['map search/folding/zoom', 'mobile layout', 'failure report UI', 'native Last-Event-ID reconnect', 'cursor committed after refresh'], connections, snapshots, screenshots: output }))
} finally {
  await browser?.close()
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
