"""Build offline, searchable HTML + editable outline + vector/raster mind map."""
from __future__ import annotations

from html import escape
from pathlib import Path
import json

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
TITLE = "FactoryPilot 从用户请求到事故报告"
BRANCHES = [
    ("01", "用户请求与登录凭证", [
        ("业务输入", ["React → POST /api/runs；JSON 包含 query 与 line_id。", "query 描述异常；line_id 决定查询范围，当前默认 LINE-02。", "文字提及的产线不会自动可靠覆盖 line_id，输入必须保持一致。"]),
        ("登录 Cookie", ["登录网关签发 JWT：sub、roles、display_name、exp。", "Set-Cookie 保存 factorypilot_token；HttpOnly 限制脚本读取。", "fetch credentials: include；浏览器按 Cookie 规则携带凭证。"]),
        ("浏览器入口", ["Docker 页面 localhost:3000 → Nginx /api → Java 网关。", "Cookie 身份与 query、line_id 等客户端业务参数分别处理。"]),
    ], [("frontend/src/api.ts", "async function request"), ("frontend/src/api.ts", "createRun:"), ("mes-gateway/src/main/java/com/factorypilot/gateway/web/AuthController.java", "public Mono<ResponseEntity<Map<String, Object>>> login")]),
    ("02", "网关认证与角色权限", [
        ("Spring Security", ["JWT 验签和有效期检查 → Authentication。", "登录等公开接口 permitAll；其余 /api/** 要求 authenticated。", "审批 POST /api/runs/*/approval 要求 SUPERVISOR 或 ADMIN。"]),
        ("匹配顺序", ["具体的审批与审计权限规则必须放在通用规则前。", "roles 映射为 ROLE_ 前缀权限；以服务端认证信息为准。"]),
        ("结果", ["未认证：401；已认证但角色不足：403。", "角色校验通过仍需校验任务状态；不代表审批业务必然成功。"]),
    ], [("mes-gateway/src/main/java/com/factorypilot/gateway/config/SecurityConfig.java", "public SecurityWebFilterChain"), ("mes-gateway/src/main/java/com/factorypilot/gateway/security/CookieBearerTokenConverter.java", "public Mono<Authentication>")]),
    ("03", "Java 转发与可信审批身份", [
        ("AgentProxyController", ["WebClient 转发创建、查询、审批、SSE 请求到 Agent Service。", "createRun 转发 JSON；响应状态与正文交回浏览器。"]),
        ("审批身份覆盖", ["body.approver = authentication.getName()。", "客户端修改 approver，无法冒充其他审批人。", "角色是否允许审批由 SecurityConfig 判断；姓名覆盖负责审计归属。"]),
        ("SSE 网关续传 · 已补齐", ["读取 after 和 Last-Event-ID，校验为非负安全整数。", "取两者较大值，作为 after 转发，保留下游 SSE id。"]),
    ], [("mes-gateway/src/main/java/com/factorypilot/gateway/web/AgentProxyController.java", "public Mono<ResponseEntity<String>> approve"), ("mes-gateway/src/main/java/com/factorypilot/gateway/web/AgentProxyController.java", "private static long resolveEventCursor")]),
    ("04", "FastAPI 创建业务任务", [
        ("请求校验", ["RunCreate 校验 query 长度和 LINE-两位数字格式。", "格式通过不等于产线存在，也不等于用户拥有该产线的数据权限。"]),
        ("持久化", ["uuid.uuid4().hex → 32 位 run_id。", "runs：query、line_id、queued、state_json={}、创建时间。", "run_events：request/completed，表示接收请求这个步骤完成。"]),
        ("202 Accepted", ["提交调度后返回任务摘要，前端持 run_id 查详情。", "queued 在投递前写入，不证明消息已进入 Celery。", "重复提交产生新 run_id；数据库写入与消息投递不在同一事务。"]),
    ], [("agent-service/src/factorypilot/main.py", "def create_run("), ("agent-service/src/factorypilot/service.py", "def create_run("), ("agent-service/src/factorypilot/database.py", "def create_run(")]),
    ("05", "后台调度与 Worker", [
        ("两种模式", ["TASK_QUEUE_BACKEND=local：响应后运行 BackgroundTasks。", "TASK_QUEUE_BACKEND=celery：发送消息 → Redis broker → Worker。", "Redis 保存消息，Worker 进程执行 Python 函数。"]),
        ("start 与 resume", ["start：factorypilot.run.start(run_id)。", "resume：factorypilot.run.resume(run_id, approval_json)。", "业务 run_id 不变；start/resume 的 Celery task_id 不同。"]),
        ("故障处理", ["Worker 各自创建 AgentService；读取持久化任务与 checkpoint。", "任务包装层最多重试 3 次；这是调度重试，不是自动重新审批。", "Celery 允许消息重投；确定性 task_id 不等于业务 exactly-once。"]),
    ], [("agent-service/src/factorypilot/task_queue.py", "class TaskDispatcher"), ("agent-service/src/factorypilot/task_queue.py", "def start_run_task("), ("agent-service/src/factorypilot/task_queue.py", "def resume_run_task(")]),
    ("06", "互斥执行与状态恢复", [
        ("执行锁", ["threading.Lock 保护 _running 集合的检查与登记。", "可选 Redis SET NX EX，以 run_id 为粒度，owner 标识持锁者。", "finally 清理登记；Lua 比较 owner 后删除锁。"]),
        ("检查点", ["SqliteSaver 接入 compile；thread_id = run_id。", "无状态：invoke(initial)；有待执行节点：invoke(None)。", "审批恢复：invoke(Command(resume=approval))。"]),
        ("边界", ["checkpoint、runs.state_json、run_events 是不同存储职责。", "Worker 重启恢复需要访问原 checkpoint；不是从 Python 某一行解冻。", "Redis 锁默认 1800 秒且无续租；跨机器 checkpoint 仍需升级。"]),
    ], [("agent-service/src/factorypilot/service.py", "def start_run("), ("agent-service/src/factorypilot/service.py", "def _claim("), ("agent-service/src/factorypilot/coordination.py", "def acquire_run_lock(")]),
    ("07", "显式图：Node、Edge、State", [
        ("16 个节点与汇合", ["START → triage → 四路并行：mes_data / alarm_data /", "maintenance_data / knowledge_data → statistical_analysis。", "再并行 graph_knowledge 与 vendor_expert，汇合到 rca → plan。", "approval → execute → verify；拒绝走 reject；最终 report → END。", "新增 verification_failed：由 verify 的条件边进入，再到 report。"]),
        ("共享 State", ["run_id、query、line_id、device_id：任务与设备范围。", "quality、alarms、maintenance、documents：原始查询结果。", "evidence 使用 operator.add 汇总；production_analysis 保存统计。", "hypotheses、action_plan、approval、action_results、verification、report。"]),
        ("职责", ["Triage / Data / RCA / Action 是职责划分，不要求独立进程或模型。", "统计程序计算基线、变化、相关性、告警重合；RCA 解释证据。"]),
    ], [("agent-service/src/factorypilot/graph.py", "class AgentState"), ("agent-service/src/factorypilot/graph.py", "def build("), ("agent-service/src/factorypilot/production_analytics.py", "def analyze_production_window(")]),
    ("08", "工具、检索与根因分析", [
        ("MCP 调用", ["mes-mcp：get_line_device / get_recent_quality /", "get_recent_alerts / get_maintenance_history。", "knowledge-mcp：search_knowledge；Milvus 或词法检索。", "TOOL_TRANSPORT=mcp 时使用真实协议；inprocess 是本地调用。"]),
        ("补充证据", ["Neo4j：参数化故障路径查询；A2A：Agent Card 与厂商服务。", "两者是独立客户端，不属于上述三个 MCP Server。", "检索保留文档、章节、版本与证据 ID；候选混合打分排序。"]),
        ("RCA 与计划", ["Demo 规则引擎，或 LLM + Pydantic 校验；引用已有 evidence ID。", "模型失败可降级为规则，并标记原因；不会跳过审批。", "当前处置动作模板化；统计相关性和模型置信度不等于因果证明。"]),
    ], [("agent-service/src/factorypilot/mcp_client_tools.py", "class MCPRemoteCaller"), ("agent-service/src/factorypilot/analysis_engine.py", "class OpenAIRcaEngine"), ("agent-service/src/factorypilot/graph.py", "def create_action_plan(")]),
    ("09", "人工审批与受控执行", [
        ("approval 暂停", ["先写 awaiting_approval 与 waiting 事件，再 interrupt 暂停。", "checkpoint 保存进度；本次调用返回并释放执行锁。", "两类存储不在同一事务，状态显示与 checkpoint 存在短暂窗口。"]),
        ("同一任务恢复", ["审批通过网关 RBAC 与任务状态检查，生成 approval_id。", "Command(resume=approval) 使 interrupt 得到人工决定。", "审批节点从头进入；其前置事件用幂等键避免重复。"]),
        ("execute / reject", ["批准：Action MCP 再查数据库审批记录，创建模拟工单、调整抽检。", "拒绝：reject → report，不调用业务写工具。", "动作白名单、参数校验、超时和审计；现有幂等仍有并发边界。"]),
    ], [("agent-service/src/factorypilot/graph.py", "def request_approval("), ("agent-service/src/factorypilot/service.py", "def resume_run("), ("agent-service/src/factorypilot/mcp_servers/action_server.py", "def _server_verified_approval(")]),
    ("10", "写后验证与失败分支 · 已补齐", [
        ("verify 读回", ["直接读数据库：run_id + device_id 对应工单，以及 line_controls。", "工单存在、产线设置存在、抽检周期=10 分钟，全部成立才通过。", "读取异常抛出到任务层；业务读回不符进入显式失败分支。"]),
        ("验证失败", ["verify → verification_failed → report；生成失败原因与人工核查建议。", "不生成模拟恢复批次、不放行、不自动重放业务写操作。", "先保存最终状态，再标记 runs.status=failed；重试不会改成成功。"]),
        ("验证通过", ["再判断模拟良率 [96.8, 97.4, 97.9]：从最新批次向前计数。", "连续 ≥3 批且每批 ≥97% 才 passed；示例仅 2 批，仍 observing。", "工单创建不等于维修完成；没有采集真实新批次或自动恢复循环。"]),
    ], [("agent-service/src/factorypilot/graph.py", "def verify_result("), ("agent-service/src/factorypilot/graph.py", "def handle_verification_failure("), ("agent-service/src/factorypilot/service.py", "def _finalize_run(")]),
    ("11", "报告、终态与页面展示", [
        ("报告持久化", ["report 汇总异常、范围、根因、审批、动作、验证与结论。", "保存 runs.report_json，再写 report/completed 时间线事件。", "report 事件完成表示报告生成成功，不代表业务处置成功。"]),
        ("最终状态", ["拒绝 → rejected；写入验证失败 → failed；写入通过 → completed。", "completed 可同时伴随质量 observing，仍需继续观察。", "失败报告带 failure_reasons、readback、next_steps，页面红色提示。"]),
        ("完成通知", ["最终图状态保存后更新终态；SSE 排空历史事件，再发送 done。", "React 最后 GET 详情并刷新列表，然后结束该事件订阅。"]),
    ], [("agent-service/src/factorypilot/graph.py", "def generate_report("), ("agent-service/src/factorypilot/service.py", "def _finalize_run("), ("frontend/src/App.tsx", "run.report &&")]),
    ("12", "SSE 续传与 React 刷新 · 已补齐", [
        ("服务端事件日志", ["事件先写 run_events；Redis Pub/Sub 仅唤醒，数据库是补发来源。", "after / Last-Event-ID → max 合法游标；查询 run_id 且 id>cursor。", "SSE 包含 id、timeline、data；心跳保活，终态发送 done。"]),
        ("浏览器消费", ["每任务游标保存到 sessionStorage；打开连接传 ?after=游标。", "原生 EventSource 自动重连发 Last-Event-ID；网关已接通。", "timeline → 串行合并 GET → setRun → 成功后提交应用游标。", "GET 失败保留旧应用游标并重试；旧事件去重，切换任务关闭旧流。"]),
        ("语义与验证", ["事件补发不会执行业务工具；不宣称业务副作用 exactly-once。", "同标签页刷新可复用游标；存储不可用时退回内存和历史重播。", "测试覆盖缺失工单、错误设置、终态重试、续传游标和前端刷新失败。"]),
    ], [("agent-service/src/factorypilot/main.py", "def resolve_event_cursor("), ("agent-service/src/factorypilot/main.py", "async def stream_events("), ("frontend/src/runEvents.ts", "export function subscribeRunEvents")]),
]


def anchors(refs):
    result = []
    for path, needle in refs:
        lines = (ROOT / path).read_text(encoding="utf-8-sig").splitlines()
        matches = [i for i, line in enumerate(lines, 1) if needle in line]
        if not matches:
            raise ValueError(f"Missing source anchor: {path}: {needle}")
        result.append((path, matches[0]))
    return result


class Canvas:
    def __init__(self, width, height):
        self.image = Image.new("RGB", (width, height), "#F6F8FB")
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{TITLE}完整思维导图"><rect width="100%" height="100%" fill="#F6F8FB"/>']
        self.fonts = {}

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc", size)
        return self.fonts[key]

    def text(self, x, y, value, size=28, color="#283A50", bold=False):
        self.draw.text((x, y), value, font=self.font(size, bold), fill=color, anchor="lm")
        self.svg.append(f'<text x="{x}" y="{y}" font-family="Microsoft YaHei, sans-serif" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" dominant-baseline="central">{escape(value)}</text>')

    def rect(self, x, y, w, h, fill, stroke="#CCD6E2", radius=20):
        self.draw.rounded_rectangle((x, y, x+w, y+h), radius=radius, fill=fill, outline=stroke, width=2)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')

    def line(self, pts, color="#8094AC", width=3):
        self.draw.line(pts, fill=color, width=width)
        self.svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in pts)}" fill="none" stroke="{color}" stroke-width="{width}"/>')

    def wrap(self, value, width, size=27):
        result, current = [], ""
        for char in value:
            if current and self.font(size).getlength(current + char) > width:
                result.append(current)
                current = ""
            current += char
        return result + ([current] if current else [])


def make_map():
    measure = Canvas(1, 1)
    layouts = []
    for number, title, groups, _ in BRANCHES:
        rows = []
        for label, leaves in groups:
            rows.append(("group", label))
            for leaf in leaves:
                rows.extend(("leaf", line) for line in measure.wrap(leaf, 1310))
        layouts.append((number, title, rows, 128 + sum(44 if kind == "group" else 36 for kind, _ in rows)))
    slots = [max(layouts[i][3], layouts[i+6][3]) + 40 for i in range(6)]
    height = 250 + sum(slots)
    canvas = Canvas(3800, height)
    canvas.text(60, 75, TITLE, 56, bold=True)
    canvas.text(60, 140, "代码核对版 · 2026-09-22 · 12 个主分支 · 16 个 LangGraph 节点 · 蓝色标记本次补齐", 30, color="#60758E")
    positions = []
    y = 210
    for i, slot in enumerate(slots):
        positions.append(y)
        y += slot
    root_y = (positions[0] + positions[-1] + slots[-1]) / 2 - 100
    canvas.rect(1660, root_y, 480, 210, "#173B61", "#173B61")
    canvas.text(1710, root_y+58, "FactoryPilot", 46, "#FFFFFF", True)
    canvas.text(1710, root_y+116, "MES 诊断与处置", 34, "#FFFFFF")
    canvas.text(1710, root_y+169, "同一 run_id 贯穿全程", 26, "#D3E4F4")
    for side in range(2):
        x, spine, anchor = (60, 1570, 1510) if side == 0 else (2290, 2230, 2290)
        centers = [positions[i] + layouts[i+side*6][3]/2 for i in range(6)]
        canvas.line([(spine, min(centers)), (spine, max(centers))], width=5)
        canvas.line([(1660 if side == 0 else 2140, root_y+105), (spine, root_y+105)], width=5)
        for i in range(6):
            number, title, rows, h = layouts[i+side*6]
            y = positions[i]
            canvas.line([(spine, centers[i]), (anchor, centers[i])])
            updated = number in {"03", "10", "12"}
            canvas.rect(x, y, 1450, h, "#FFFFFF", "#5B91BA" if updated else "#CCD6E2")
            canvas.rect(x+20, y+20, 1410, 68, "#EAF3FB" if updated else "#EDF1F6", "#EAF3FB" if updated else "#EDF1F6", 12)
            canvas.text(x+42, y+53, f"{number}  {title}", 35, "#173B61", True)
            cursor = y+112
            group_y = None
            for kind, text in rows:
                if kind == "group":
                    if group_y is not None:
                        canvas.line([(x+49, group_y+17), (x+49, cursor-30)], "#C6D2E0", 2)
                    group_y = cursor
                    canvas.text(x+38, cursor, text, 29, "#173B61", True)
                    cursor += 44
                else:
                    canvas.line([(x+49, cursor), (x+67, cursor)], "#C6D2E0", 2)
                    canvas.text(x+80, cursor, text, 27)
                    cursor += 36
            if group_y is not None:
                canvas.line([(x+49, group_y+17), (x+49, cursor-36)], "#C6D2E0", 2)
    canvas.text(60, height-30, "用途：按节点与代码索引复述执行链路。模拟质量、模板计划和并发边界均保留真实说明；不要将演示结果描述为真实工厂验证。", 25, "#60758E")
    svg = "\n".join(canvas.svg) + "</svg>"
    (ASSETS / "factorypilot_execution_mindmap.svg").write_text(svg, encoding="utf-8")
    canvas.image.save(ASSETS / "factorypilot_execution_mindmap.png", dpi=(220, 220))
    preview = canvas.image.copy()
    preview.thumbnail((1500, 1800))
    preview.save(ASSETS / "factorypilot_execution_mindmap_preview.png")
    return svg, height


def main():
    ASSETS.mkdir(exist_ok=True)
    svg, height = make_map()
    outlines, sections, navigation = [], [], []
    for number, title, groups, refs in BRANCHES:
        outlines.append(f"## {number} {title}\n")
        section = [f'<details class="branch" id="branch-{number}" open><summary><span>{number}</span>{escape(title)}</summary><div class="branch-content">']
        for label, leaves in groups:
            outlines.append(f"### {label}\n")
            outlines.extend(f"- {leaf}" for leaf in leaves)
            outlines.append("")
            section.append(f'<h3>{escape(label)}</h3><ul>' + "".join(f'<li>{escape(leaf)}</li>' for leaf in leaves) + '</ul>')
        links = anchors(refs)
        outlines.append("代码定位：\n")
        outlines.extend(f"- `{path}:{line}`" for path, line in links)
        outlines.append("")
        section.append('<div class="sources"><strong>代码定位</strong>' + "".join(f'<code>{escape(path)}:{line}</code>' for path, line in links) + '</div></div></details>')
        sections.append("".join(section))
        navigation.append(f'<a href="#branch-{number}">{number} {escape(title)}</a>')
    markdown = f"# {TITLE}：完整思维导图\n\n代码核对日期：2026-09-22。此文件可作为 Markdown 大纲导入支持该格式的思维导图软件。\n\n" + "\n".join(outlines)
    markdown += "\n## 本次变更验收\n\n- 写入未生效 → verification_failed → 失败报告 → runs.status=failed。\n- 写入正常但只有两批连续达标 → 质量 observing；工作流可 completed。\n- 数据库事件持久化、after 与 Last-Event-ID 接通、浏览器按任务保存成功应用的游标。\n- 节点数从 15 增至 16；新增失败分支没有自动重放写工具。\n- 既有 Word 手册若写着‘写入验证失败仍直接 completed’或‘尚无前端游标’，这两项描述已过时；以本导图和本次源码为准。\n\n## 回归证据\n\n- Python：`python -m pytest agent-service/tests -q`（使用隔离测试数据库、Demo 模式与关闭外部追踪）。\n- Java：`cd mes-gateway`，`mvn test`。\n- 前端：Node.js 18+，`cd frontend`，`npm test` 和 `npm run build`。\n- 新增故障测试：`test_verification_failure.py`；续传测试：`test_sse_resume.py`、`AgentProxyEventsTest.java`、`runEvents.test.mjs`。\n\n## 仍然存在的边界\n\n- 验证失败生成报告并人工核查，不自动回滚已有业务写入。\n- 质量数据仍为固定模拟值；没有新批次采集或自动修复循环。\n- 执行动作模板化；图中没有 verify→plan 边。\n- 任务落库与投递未组成原子事务；Redis 锁无续租。\n- 原有写工具幂等是查询后写入，不能宣称并发下 exactly-once。\n- 事件游标续传不等于消息绝不重复；前端去重与数据库补发共同工作。\n"
    (DOCS / "FactoryPilot执行链路思维导图.md").write_text(markdown, encoding="utf-8")
    html = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FactoryPilot 执行链路思维导图</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#263548;font:15px/1.8 "Microsoft YaHei",system-ui,sans-serif}header{background:#fff;border-bottom:1px solid #dce3eb;padding:26px 36px}h1{font-size:27px;line-height:1.4;margin:0 0 8px}header p{margin:0;color:#617187}nav.toolbar{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:10px;padding:13px 36px;background:#fff;border-bottom:1px solid #dce3eb}button,a.download{font:inherit;font-size:14px;border:1px solid #c8d3df;border-radius:6px;background:#fff;color:#244968;padding:6px 13px;cursor:pointer;text-decoration:none}button.active{background:#214e75;color:white;border-color:#214e75}input{font:inherit;padding:7px 12px;border:1px solid #c8d3df;border-radius:6px;min-width:240px;flex:1}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #83b8e4;outline-offset:2px}.layout{max-width:1600px;margin:auto;display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px;padding:28px 36px}.index{position:sticky;top:94px;align-self:start}.index a{display:block;padding:9px 8px;border-bottom:1px solid #dce3eb;color:#355574;text-decoration:none;font-size:13px}.index a:hover{background:#e7edf4}.branches{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;align-items:start}.branch{background:white;border:1px solid #d7e0e9;border-radius:8px;overflow:hidden;scroll-margin-top:95px}.branch summary{cursor:pointer;padding:17px 16px;color:#193d5f;font-weight:700;line-height:1.55}.branch summary span{display:inline-block;margin-right:10px;font-variant-numeric:tabular-nums;color:#6b7f92}.branch-content{padding:0 20px 20px}.branch h3{font-size:14px;margin:16px 0 8px;border-left:3px solid #4c86ad;padding-left:10px}.branch ul{margin:0;padding:0 0 0 24px;border-left:1px solid #dce5ee;margin-left:1px}.branch li{margin:5px 0;font-size:13px}.sources{margin-top:20px;padding:10px;background:#f5f7fa;font-size:11px}.sources code{display:block;overflow-wrap:anywhere;color:#5c7086}.notice{padding:16px 20px;margin:0 0 22px;border:1px solid #c6d8e6;background:#eaf2f8;border-radius:7px;font-size:14px}.map-tools{padding:16px 36px;display:flex;gap:10px;align-items:center}.viewport{height:calc(100vh - 250px);min-height:400px;overflow:auto;background:#e8edf3;border-top:1px solid #d3dde7;padding:20px}.viewport svg{display:block;max-width:none;background:white;box-shadow:0 2px 14px #0001}.limits{grid-column:1/-1;border-left:4px solid #b08339;background:#fffaf0;padding:18px 24px;font-size:13px}.limits p{margin:5px 0}footer{padding:20px 36px;color:#6a7a8d;font-size:12px}[hidden]{display:none!important}@media(max-width:1000px){.layout{grid-template-columns:1fr;padding:20px}.index{display:none}.branches{grid-template-columns:1fr}header,nav.toolbar{padding-left:20px;padding-right:20px}}@media print{nav.toolbar,.index,.map-tools,footer{display:none}.layout{display:block;padding:0}.branches{display:block}.branch{break-inside:avoid;margin:12px 0}.viewport{height:auto;overflow:visible}header{padding:0 0 15px}}
</style></head><body><header><h1>FactoryPilot · 从用户请求到事故报告</h1><p>完整思维导图｜源码核对 2026-09-22｜12 个主分支 · 16 个工作流节点｜本次补齐：写后验证失败处理、SSE 游标续传</p></header>
<nav class="toolbar" aria-label="阅读工具"><button id="outlineBtn" class="active">分支阅读</button><button id="mapBtn">总览导图</button><button id="expandBtn">全部展开</button><button id="collapseBtn">全部折叠</button><input id="search" type="search" aria-label="搜索分支内容" placeholder="搜索节点、State、MCP、游标…"><a class="download" href="assets/factorypilot_execution_mindmap.svg" download>下载 SVG</a><a class="download" href="assets/factorypilot_execution_mindmap.png" download>高清 PNG</a><a class="download" href="FactoryPilot执行链路思维导图.md" download>Markdown</a></nav>
<section id="outlineView" class="layout"><aside class="index" aria-label="分支目录">__NAV__</aside><main><div class="notice">沿编号追踪代码：每个分支包含具体方法、状态字段和真实边界。写入验证失败会进入失败报告；正常写入与质量恢复分别判断。点击分支标题可折叠。</div><div class="branches">__SECTIONS__<section class="limits"><strong>面试表述边界</strong><p>当前是模拟 MES 闭环：工单创建不代表检修完成；固定三批良率不代表真实产线恢复；没有自动重新规划和执行的循环。</p><p>新增失败分支不会自动回滚或重复业务写入。SQLite checkpoint、Redis 锁无续租、落库与投递分离、原有并发幂等限制仍需如实说明。</p><p>既有手册中“verify 失败仍完成”和“SSE 尚未接通游标”已过时；以本版和更新源码为准。</p></section></div></main></section>
<section id="mapView" hidden><div class="map-tools"><button id="fitBtn">适应宽度</button><button id="minusBtn" aria-label="缩小导图">−</button><output id="scale">100%</output><button id="plusBtn" aria-label="放大导图">＋</button><button id="actualBtn">原始大小</button><span>导图可缩放和滚动；逐条阅读推荐“分支阅读”。</span></div><div class="viewport" id="viewport">__SVG__</div></section><footer>本文件离线可用，无外部脚本依赖。Markdown 可编辑，SVG 可无损缩放；代码定位行号对应本次生成时的源码。</footer>
<script>
const $=id=>document.getElementById(id);const branches=[...document.querySelectorAll('.branch')];let zoom=1;
function view(map){$('mapView').hidden=!map;$('outlineView').hidden=map;$('mapBtn').classList.toggle('active',map);$('outlineBtn').classList.toggle('active',!map);if(map)fit()}
function scale(v){zoom=Math.max(.15,Math.min(2,v));const svg=$('viewport').querySelector('svg');svg.style.width=(3800*zoom)+'px';svg.style.height=(__HEIGHT__*zoom)+'px';$('scale').value=Math.round(zoom*100)+'%'}
function fit(){scale(($('viewport').clientWidth-40)/3800)}
$('outlineBtn').onclick=()=>view(false);$('mapBtn').onclick=()=>view(true);$('fitBtn').onclick=fit;$('minusBtn').onclick=()=>scale(zoom/1.25);$('plusBtn').onclick=()=>scale(zoom*1.25);$('actualBtn').onclick=()=>scale(1);
$('expandBtn').onclick=()=>branches.forEach(b=>b.open=true);$('collapseBtn').onclick=()=>branches.forEach(b=>b.open=false);
$('search').oninput=event=>{if(!$('mapView').hidden)view(false);const q=event.target.value.trim().toLowerCase();branches.forEach(b=>{b.hidden=!!q&&!b.textContent.toLowerCase().includes(q);if(q&&!b.hidden)b.open=true})};
document.querySelectorAll('.index a').forEach(a=>a.onclick=()=>{const target=document.querySelector(a.getAttribute('href'));target.hidden=false;target.open=true});
</script></body></html>'''
    html = html.replace("__NAV__", "".join(navigation)).replace("__SECTIONS__", "".join(sections)).replace("__SVG__", svg).replace("__HEIGHT__", str(height))
    (DOCS / "FactoryPilot执行链路思维导图.html").write_text(html, encoding="utf-8")
    print(json.dumps({"branches": len(BRANCHES), "source_anchors": sum(len(b[3]) for b in BRANCHES), "svg_size": [3800, height], "html": str(DOCS / "FactoryPilot执行链路思维导图.html")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
