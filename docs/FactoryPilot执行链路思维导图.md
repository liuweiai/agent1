# FactoryPilot 从用户请求到事故报告：完整思维导图

代码核对日期：2026-09-22。此文件可作为 Markdown 大纲导入支持该格式的思维导图软件。

## 01 用户请求与登录凭证

### 业务输入

- React → POST /api/runs；JSON 包含 query 与 line_id。
- query 描述异常；line_id 决定查询范围，当前默认 LINE-02。
- 文字提及的产线不会自动可靠覆盖 line_id，输入必须保持一致。

### 登录 Cookie

- 登录网关签发 JWT：sub、roles、display_name、exp。
- Set-Cookie 保存 factorypilot_token；HttpOnly 限制脚本读取。
- fetch credentials: include；浏览器按 Cookie 规则携带凭证。

### 浏览器入口

- Docker 页面 localhost:3000 → Nginx /api → Java 网关。
- Cookie 身份与 query、line_id 等客户端业务参数分别处理。

代码定位：

- `frontend/src/api.ts:5`
- `frontend/src/api.ts:38`
- `mes-gateway/src/main/java/com/factorypilot/gateway/web/AuthController.java:32`

## 02 网关认证与角色权限

### Spring Security

- JWT 验签和有效期检查 → Authentication。
- 登录等公开接口 permitAll；其余 /api/** 要求 authenticated。
- 审批 POST /api/runs/*/approval 要求 SUPERVISOR 或 ADMIN。

### 匹配顺序

- 具体的审批与审计权限规则必须放在通用规则前。
- roles 映射为 ROLE_ 前缀权限；以服务端认证信息为准。

### 结果

- 未认证：401；已认证但角色不足：403。
- 角色校验通过仍需校验任务状态；不代表审批业务必然成功。

代码定位：

- `mes-gateway/src/main/java/com/factorypilot/gateway/config/SecurityConfig.java:59`
- `mes-gateway/src/main/java/com/factorypilot/gateway/security/CookieBearerTokenConverter.java:13`

## 03 Java 转发与可信审批身份

### AgentProxyController

- WebClient 转发创建、查询、审批、SSE 请求到 Agent Service。
- createRun 转发 JSON；响应状态与正文交回浏览器。

### 审批身份覆盖

- body.approver = authentication.getName()。
- 客户端修改 approver，无法冒充其他审批人。
- 角色是否允许审批由 SecurityConfig 判断；姓名覆盖负责审计归属。

### SSE 网关续传 · 已补齐

- 读取 after 和 Last-Event-ID，校验为非负安全整数。
- 取两者较大值，作为 after 转发，保留下游 SSE id。

代码定位：

- `mes-gateway/src/main/java/com/factorypilot/gateway/web/AgentProxyController.java:45`
- `mes-gateway/src/main/java/com/factorypilot/gateway/web/AgentProxyController.java:69`

## 04 FastAPI 创建业务任务

### 请求校验

- RunCreate 校验 query 长度和 LINE-两位数字格式。
- 格式通过不等于产线存在，也不等于用户拥有该产线的数据权限。

### 持久化

- uuid.uuid4().hex → 32 位 run_id。
- runs：query、line_id、queued、state_json={}、创建时间。
- run_events：request/completed，表示接收请求这个步骤完成。

### 202 Accepted

- 提交调度后返回任务摘要，前端持 run_id 查详情。
- queued 在投递前写入，不证明消息已进入 Celery。
- 重复提交产生新 run_id；数据库写入与消息投递不在同一事务。

代码定位：

- `agent-service/src/factorypilot/main.py:99`
- `agent-service/src/factorypilot/service.py:91`
- `agent-service/src/factorypilot/database.py:402`

## 05 后台调度与 Worker

### 两种模式

- TASK_QUEUE_BACKEND=local：响应后运行 BackgroundTasks。
- TASK_QUEUE_BACKEND=celery：发送消息 → Redis broker → Worker。
- Redis 保存消息，Worker 进程执行 Python 函数。

### start 与 resume

- start：factorypilot.run.start(run_id)。
- resume：factorypilot.run.resume(run_id, approval_json)。
- 业务 run_id 不变；start/resume 的 Celery task_id 不同。

### 故障处理

- Worker 各自创建 AgentService；读取持久化任务与 checkpoint。
- 任务包装层最多重试 3 次；这是调度重试，不是自动重新审批。
- Celery 允许消息重投；确定性 task_id 不等于业务 exactly-once。

代码定位：

- `agent-service/src/factorypilot/task_queue.py:84`
- `agent-service/src/factorypilot/task_queue.py:52`
- `agent-service/src/factorypilot/task_queue.py:68`

## 06 互斥执行与状态恢复

### 执行锁

- threading.Lock 保护 _running 集合的检查与登记。
- 可选 Redis SET NX EX，以 run_id 为粒度，owner 标识持锁者。
- finally 清理登记；Lua 比较 owner 后删除锁。

### 检查点

- SqliteSaver 接入 compile；thread_id = run_id。
- 无状态：invoke(initial)；有待执行节点：invoke(None)。
- 审批恢复：invoke(Command(resume=approval))。

### 边界

- checkpoint、runs.state_json、run_events 是不同存储职责。
- Worker 重启恢复需要访问原 checkpoint；不是从 Python 某一行解冻。
- Redis 锁默认 1800 秒且无续租；跨机器 checkpoint 仍需升级。

代码定位：

- `agent-service/src/factorypilot/service.py:104`
- `agent-service/src/factorypilot/service.py:239`
- `agent-service/src/factorypilot/coordination.py:84`

## 07 显式图：Node、Edge、State

### 16 个节点与汇合

- START → triage → 四路并行：mes_data / alarm_data /
- maintenance_data / knowledge_data → statistical_analysis。
- 再并行 graph_knowledge 与 vendor_expert，汇合到 rca → plan。
- approval → execute → verify；拒绝走 reject；最终 report → END。
- 新增 verification_failed：由 verify 的条件边进入，再到 report。

### 共享 State

- run_id、query、line_id、device_id：任务与设备范围。
- quality、alarms、maintenance、documents：原始查询结果。
- evidence 使用 operator.add 汇总；production_analysis 保存统计。
- hypotheses、action_plan、approval、action_results、verification、report。

### 职责

- Triage / Data / RCA / Action 是职责划分，不要求独立进程或模型。
- 统计程序计算基线、变化、相关性、告警重合；RCA 解释证据。

代码定位：

- `agent-service/src/factorypilot/graph.py:20`
- `agent-service/src/factorypilot/graph.py:64`
- `agent-service/src/factorypilot/production_analytics.py:44`

## 08 工具、检索与根因分析

### MCP 调用

- mes-mcp：get_line_device / get_recent_quality /
- get_recent_alerts / get_maintenance_history。
- knowledge-mcp：search_knowledge；Milvus 或词法检索。
- TOOL_TRANSPORT=mcp 时使用真实协议；inprocess 是本地调用。

### 补充证据

- Neo4j：参数化故障路径查询；A2A：Agent Card 与厂商服务。
- 两者是独立客户端，不属于上述三个 MCP Server。
- 检索保留文档、章节、版本与证据 ID；候选混合打分排序。

### RCA 与计划

- Demo 规则引擎，或 LLM + Pydantic 校验；引用已有 evidence ID。
- 模型失败可降级为规则，并标记原因；不会跳过审批。
- 当前处置动作模板化；统计相关性和模型置信度不等于因果证明。

代码定位：

- `agent-service/src/factorypilot/mcp_client_tools.py:17`
- `agent-service/src/factorypilot/analysis_engine.py:75`
- `agent-service/src/factorypilot/graph.py:424`

## 09 人工审批与受控执行

### approval 暂停

- 先写 awaiting_approval 与 waiting 事件，再 interrupt 暂停。
- checkpoint 保存进度；本次调用返回并释放执行锁。
- 两类存储不在同一事务，状态显示与 checkpoint 存在短暂窗口。

### 同一任务恢复

- 审批通过网关 RBAC 与任务状态检查，生成 approval_id。
- Command(resume=approval) 使 interrupt 得到人工决定。
- 审批节点从头进入；其前置事件用幂等键避免重复。

### execute / reject

- 批准：Action MCP 再查数据库审批记录，创建模拟工单、调整抽检。
- 拒绝：reject → report，不调用业务写工具。
- 动作白名单、参数校验、超时和审计；现有幂等仍有并发边界。

代码定位：

- `agent-service/src/factorypilot/graph.py:447`
- `agent-service/src/factorypilot/service.py:156`
- `agent-service/src/factorypilot/mcp_servers/action_server.py:27`

## 10 写后验证与失败分支 · 已补齐

### verify 读回

- 直接读数据库：run_id + device_id 对应工单，以及 line_controls。
- 工单存在、产线设置存在、抽检周期=10 分钟，全部成立才通过。
- 读取异常抛出到任务层；业务读回不符进入显式失败分支。

### 验证失败

- verify → verification_failed → report；生成失败原因与人工核查建议。
- 不生成模拟恢复批次、不放行、不自动重放业务写操作。
- 先保存最终状态，再标记 runs.status=failed；重试不会改成成功。

### 验证通过

- 再判断模拟良率 [96.8, 97.4, 97.9]：从最新批次向前计数。
- 连续 ≥3 批且每批 ≥97% 才 passed；示例仅 2 批，仍 observing。
- 工单创建不等于维修完成；没有采集真实新批次或自动恢复循环。

代码定位：

- `agent-service/src/factorypilot/graph.py:491`
- `agent-service/src/factorypilot/graph.py:533`
- `agent-service/src/factorypilot/service.py:206`

## 11 报告、终态与页面展示

### 报告持久化

- report 汇总异常、范围、根因、审批、动作、验证与结论。
- 保存 runs.report_json，再写 report/completed 时间线事件。
- report 事件完成表示报告生成成功，不代表业务处置成功。

### 最终状态

- 拒绝 → rejected；写入验证失败 → failed；写入通过 → completed。
- completed 可同时伴随质量 observing，仍需继续观察。
- 失败报告带 failure_reasons、readback、next_steps，页面红色提示。

### 完成通知

- 最终图状态保存后更新终态；SSE 排空历史事件，再发送 done。
- React 最后 GET 详情并刷新列表，然后结束该事件订阅。

代码定位：

- `agent-service/src/factorypilot/graph.py:546`
- `agent-service/src/factorypilot/service.py:206`
- `frontend/src/App.tsx:559`

## 12 SSE 续传与 React 刷新 · 已补齐

### 服务端事件日志

- 事件先写 run_events；Redis Pub/Sub 仅唤醒，数据库是补发来源。
- after / Last-Event-ID → max 合法游标；查询 run_id 且 id>cursor。
- SSE 包含 id、timeline、data；心跳保活，终态发送 done。

### 浏览器消费

- 每任务游标保存到 sessionStorage；打开连接传 ?after=游标。
- 原生 EventSource 自动重连发 Last-Event-ID；网关已接通。
- timeline → 串行合并 GET → setRun → 成功后提交应用游标。
- GET 失败保留旧应用游标并重试；旧事件去重，切换任务关闭旧流。

### 语义与验证

- 事件补发不会执行业务工具；不宣称业务副作用 exactly-once。
- 同标签页刷新可复用游标；存储不可用时退回内存和历史重播。
- 测试覆盖缺失工单、错误设置、终态重试、续传游标和前端刷新失败。

代码定位：

- `agent-service/src/factorypilot/main.py:23`
- `agent-service/src/factorypilot/main.py:173`
- `frontend/src/runEvents.ts:27`

## 本次变更验收

- 写入未生效 → verification_failed → 失败报告 → runs.status=failed。
- 写入正常但只有两批连续达标 → 质量 observing；工作流可 completed。
- 数据库事件持久化、after 与 Last-Event-ID 接通、浏览器按任务保存成功应用的游标。
- 节点数从 15 增至 16；新增失败分支没有自动重放写工具。
- 既有 Word 手册若写着‘写入验证失败仍直接 completed’或‘尚无前端游标’，这两项描述已过时；以本导图和本次源码为准。

## 回归证据

- Python：`python -m pytest agent-service/tests -q`（使用隔离测试数据库、Demo 模式与关闭外部追踪）。
- Java：`cd mes-gateway`，`mvn test`。
- 前端：Node.js 18+，`cd frontend`，`npm test` 和 `npm run build`。
- 新增故障测试：`test_verification_failure.py`；续传测试：`test_sse_resume.py`、`AgentProxyEventsTest.java`、`runEvents.test.mjs`。

## 仍然存在的边界

- 验证失败生成报告并人工核查，不自动回滚已有业务写入。
- 质量数据仍为固定模拟值；没有新批次采集或自动修复循环。
- 执行动作模板化；图中没有 verify→plan 边。
- 任务落库与投递未组成原子事务；Redis 锁无续租。
- 原有写工具幂等是查询后写入，不能宣称并发下 exactly-once。
- 事件游标续传不等于消息绝不重复；前端去重与数据库补发共同工作。
