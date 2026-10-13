# Graph / Tool / MCP 源码核查素材

核查日期：2026-09-17。仅静态读源码，没有联网、运行模型或运行应用。本文是主任务撰写手册使用的源码事实材料。以下 `graph.py` 等短文件名，均指 `D:/秋招项目MESAI/agent-service/src/factorypilot/` 下对应文件；所有行号按本次工作区源码核对。

## 1. 组装入口：先确定注入的是哪一个工具实现

`service.py:37 AgentService.__init__`：

1. `service.py:40` 通过 `mysql_database.py:180 create_database(settings)` 创建业务库：`data_backend == "mysql"` 返回 `MySQLDatabase(settings.database_url)`，否则返回 SQLite `Database(settings.database_path)`。
2. `service.py:44-47` 独立创建 SQLite 连接和 `SqliteSaver`。业务库可以是 MySQL，但当前 checkpoint 仍是 SQLite。
3. `service.py:48-59` 仅由 `settings.agent_mode == "llm"` 决定 `OpenAIRcaEngine` / `DemoRcaEngine`；不能笼统说“有 Key 就自动用模型”。
4. `service.py:60-65` 由 `tool_transport` 决定工具对象：

   ```python
   if settings.tool_transport == "mcp":
       mes_tools, knowledge_tools, action_tools = create_remote_tools(settings)
   else:
       mes_tools = MesReadTools(self.database)
       knowledge_tools = create_knowledge_tools(self.database, settings)
       action_tools = SafeActionTools(self.database)
   ```

5. `service.py:66-67` 无论工具 transport 为哪一种，都另外构造 Neo4j 工具和 A2A client。它们不经过三个 MCP server。
6. `service.py:68-77` 将数据库、三类工具、RCA 引擎、Neo4j 工具、A2A client 注入 `FactoryPilotGraph`，再 `factory.build(self.checkpointer)`。

`config.py:54` 源码默认 `tool_transport="inprocess"`，`config.py:42` 默认 `knowledge_backend="lexical"`。这只说明配置默认值，不能代替运行环境配置。

## 2. 图的全部节点、输入及返回增量

注册源头：`graph.py:64 FactoryPilotGraph.build`，`graph.py:66-80` 注册以下 15 个节点。START / END 是哨兵，不计入 15 个业务节点。表中“输入”列包含直接读取的 State 字段；每个 `_event` 都额外使用 `run_id`。所有普通节点返回部分状态增量，不是返回全量 State。

| 节点名 | Python 方法与起始行 | 读取 State | 返回的 State 增量 | 真实计算或调用 |
| --- | --- | --- | --- | --- |
| `triage` | `graph.py:113 triage` | `run_id, query, line_id` | `anomaly_type, severity, device_id`（118、128） | `triage.py:4 classify_anomaly(query)`；设备 ID 是 `f"DEV-L{line_id[-2:]}-LASER"`；写 runs.status/runs.severity |
| `mes_data` | `graph.py:130 collect_mes_data` | `run_id, line_id` | `device_context, quality, evidence`（162） | 132 `mes.get_line_device`，133 `mes.get_recent_quality(..., 20)`；同一个节点内顺序调用两次 |
| `alarm_data` | `graph.py:164 collect_alarm_data` | `run_id, device_id` | `alarms, evidence`（179） | 166 `mes.get_recent_alerts(..., 24, 20)`；仅前 6 条告警包装为 evidence |
| `maintenance_data` | `graph.py:181 collect_maintenance_data` | `run_id, device_id` | `maintenance, evidence`（196） | 183 `mes.get_maintenance_history(..., 10)`；仅前 5 条维修记录包装为 evidence |
| `knowledge_data` | `graph.py:198 collect_knowledge_data` | `run_id, query` | `documents, evidence`（213） | 200 `knowledge.search(run_id, query + " 光路 镜片 审批", 4)` |
| `statistical_analysis` | `graph.py:215 analyze_production_data` | `run_id, quality, alarms` | `production_analysis, evidence`（283） | 223 `production_analytics.py:44 analyze_production_window`；新增 3 条统计证据 |
| `graph_knowledge` | `graph.py:285 collect_graph_knowledge` | `run_id, device_context, query` | 启用时 `graph_context, evidence`（328）；禁用时只有 `graph_context: []`（294） | 302 取设备型号，空值回退 LM-5000；303 `graph_knowledge.search_fault_paths(..., 4)` |
| `vendor_expert` | `graph.py:330 consult_vendor_expert` | `run_id, query, device_id, device_context, alarms, production_analysis` | 成功时 `vendor_expert, evidence`（392）；禁用/无结果时只有 `vendor_expert: {}`（339、364） | 348 构造 context，355 `vendor_expert.consult`；前 10 个告警码送出；成功新增 `A2A-VENDOR-1` |
| `rca` | `graph.py:394 root_cause_analysis` | 将整个 `state` 交给引擎；事件读 `run_id` | `hypotheses`（416） | 396 `rca_engine.analyze(state)`；Demo / LLM 两种实现 |
| `plan` | `graph.py:418 create_action_plan` | `run_id, device_id, line_id` | `action_plan, risk_level`（439） | 420 固定两项模板动作；436 固定 `risk_level="high"`；不读取 hypotheses、不调用模型 |
| `approval` | `graph.py:441 request_approval` | `run_id, risk_level, action_plan`；审批决定来自 interrupt 恢复输入 | `Command(update={"approval": approval}, goto=...)`（460） | 442 写 awaiting_approval；444 interrupt；452 组装 approval；459 写 runs.approval_json |
| `execute` | `graph.py:462 execute_actions` | `run_id, device_id, line_id, approval` | `action_results`（479） | 466 创建工单，474 加严抽检；两次顺序执行；**不读取或遍历 action_plan** |
| `reject` | `graph.py:481 reject_actions` | `run_id, approval` | `action_results: []`（483） | 只发 skipped 事件，事件 step 是 `execute`，不是 `reject`；不调用写工具 |
| `verify` | `graph.py:485 verify_result` | `run_id, line_id, production_analysis` | `verification, production_analysis`（502） | 487/488 直接 DB 查工单和线控；489 固定三批；490 evaluate_recovery |
| `report` | `graph.py:504 generate_report` | `run_id, query, line_id, device_id, severity, hypotheses, evidence, approval, action_results, verification, production_analysis` | `report`（526） | 506 Python 字典组装报告，521 写 runs.report_json；不调用模型 |

`graph.py:100 _event` 的调用链统一为 `self.database.emit_event(run_id, step, title, status, detail, payload, f"{step}:{status}")`（109-111）。这是业务事件持久化，不是 MCP 工具调用。`database.py:429 emit_event` 将记录写入 **run_events**，并仅在插入成功时通知 event_sink（454）。

## 3. Edge 的准确源码，以及画图时应保留的汇合语义

下面摘自 `graph.py:82-98`：

```python
builder.add_edge(START, "triage")
for node in ("mes_data", "alarm_data", "maintenance_data", "knowledge_data"):
    builder.add_edge("triage", node)
builder.add_edge(
    ["mes_data", "alarm_data", "maintenance_data", "knowledge_data"],
    "statistical_analysis",
)
builder.add_edge("statistical_analysis", "graph_knowledge")
builder.add_edge("statistical_analysis", "vendor_expert")
builder.add_edge(["graph_knowledge", "vendor_expert"], "rca")
builder.add_edge("rca", "plan")
builder.add_edge("plan", "approval")
builder.add_edge("execute", "verify")
builder.add_edge("verify", "report")
builder.add_edge("reject", "report")
builder.add_edge("report", END)
return builder.compile(checkpointer=checkpointer)
```

关键点：列表起点的 `add_edge` 表达四路/两路全部完成才继续的汇合。两个并行段分别是四个采集节点，以及 Neo4j + A2A；不是 15 个节点全并行。Neo4j/A2A 即使禁用，也执行对应节点并返回空字段，再参与汇合。

approval 没有 `add_conditional_edges`，也没有注册静态的 approval→execute / approval→reject；跳转发生在 `graph.py:460`：

```python
return Command(
    update={"approval": approval},
    goto="execute" if approval["approved"] else "reject",
)
```

纸上拓扑可画为：

```text
START → triage
          ├ mes_data ──────────┐
          ├ alarm_data ────────┤
          ├ maintenance_data ──┤ → statistical_analysis
          └ knowledge_data ────┘       ├ graph_knowledge ──┐
                                       └ vendor_expert ───┘ → rca → plan → approval [interrupt]
                                                                            ├ 批准 → execute → verify → report → END
                                                                            └ 拒绝 → reject ───────────→ report → END
```

当前没有从 verify 回到 plan 的循环，也没有持续采样等待下一批的图节点。

## 4. State 数据契约与合并方式

`graph.py:20 AgentState(TypedDict, total=False)` 共 22 个字段：

```text
run_id, query, line_id, device_id, anomaly_type, severity, risk_level,
device_context, alarms, quality, maintenance, documents,
production_analysis, graph_context, vendor_expert,
evidence, hypotheses, action_plan, approval, action_results, verification, report
```

其中唯一显式 reducer 在 `graph.py:36`：

```python
evidence: Annotated[list[dict[str, Any]], operator.add]
```

每个 evidence 生产节点只返回本节点新增列表；合并时做列表相加，不去重。其他字段没有这个 reducer，通常覆盖旧值。四个并行采集节点和两个并行补充节点各写自己的专属字段，只共享追加 evidence，因此不存在正常并行路径下对同一普通字段的竞争写入。

初始输入在 `service.py:113-118` 只有 `run_id, query, line_id, evidence=[]`。State **没有** `status/current_step/error`；业务状态和错误信息写在 runs 表。State 是 TypedDict，不是 Pydantic 模型。

`production_analysis` 在 statistical_analysis 产生，在 verify 中 `dict(...)` 复制后把 `recovery_validation` 替换为本次恢复判断，再整体返回覆盖。图中累加 evidence 的节点：mes_data、alarm_data、maintenance_data、knowledge_data、statistical_analysis、graph_knowledge（启用）、vendor_expert（成功）。RCA 只消费证据，不新增 evidence。

## 5. 每次 Node → Python adapter → MCP → server → backend 的映射

### 5.1 所有远程工具共同经过的代码

`mcp_client_tools.py:88 create_remote_tools`：为 MES/Knowledge/Action 各创建一个 `MCPRemoteCaller`，分别使用 `settings.mes_mcp_url / knowledge_mcp_url / action_mcp_url`（95-97）。

公共远程链：

```text
Remote*.业务方法
→ MCPRemoteCaller.call(name, arguments)                 mcp_client_tools.py:48
→ asyncio.run(self._call_async(name, arguments))        mcp_client_tools.py:52
→ httpx2.AsyncClient(...)                              mcp_client_tools.py:25
→ streamable_http_client(self.url, http_client=...)     mcp_client_tools.py:28
→ Client(transport, read_timeout_seconds=...)           mcp_client_tools.py:29
→ client.call_tool(name, arguments, ...)                mcp_client_tools.py:31
→ MCP Server @mcp.tool 对应的同名函数
→ 服务端 tools.py / milvus_knowledge.py 业务实现
→ 数据库 / Milvus
```

`call` 会按 `max_attempts` 重试（50-58），退避为 `0.15 * 2 ** (attempt - 1)` 秒（56）；单次 call_tool 外层还有 `asyncio.wait_for`（30-33）。不是所有 inprocess 工具都具有这层网络重试。

响应解包顺序（34-46）：`is_error` 抛错；如果 `structured_content` 是仅含 `result` 的字典就返回其 result；否则直接返回 structured_content；没有结构化结果则找 text block 并 JSON 解码。

服务端入口：`mcp_servers/common.py:21 run_server`。`MCP_TRANSPORT=streamable-http` 时 `server.run(..., stateless_http=True, json_response=True)`；否则默认 stdio（22、42-43）。**本项目的 RemoteCaller 只实现 Streamable HTTP，不会自动连接 stdio。**

服务端数据库由 `mcp_servers/common.py:13 create_database` 构建，仍通过相同 `create_configured_database(settings)` 选 MySQL/SQLite，并 initialize。远程模式下 server 是独立进程，但底层业务数据应指向与 Agent 一致的数据库配置，尤其审批复核和 verify 需要读到同一批数据。

### 5.2 MES 四个工具（mes_data 调两个）

| Node 调用点 | 客户端 Python adapter | 传给 `call_tool` 的 name | MCP server 函数 | 实际业务实现 | 数据表与查询方式 |
| --- | --- | --- | --- | --- | --- |
| `graph.py:132 mes_data` | `mcp_client_tools.py:64 RemoteMesTools.get_line_device` | `get_line_device`（65） | `mcp_servers/mes_server.py:28 get_line_device` | `tools.py:45 MesReadTools.get_line_device` | `devices WHERE line_id=?`，fetch_one（51-53） |
| `graph.py:133 mes_data` | `mcp_client_tools.py:68 RemoteMesTools.get_recent_quality` | `get_recent_quality`（69） | `mcp_servers/mes_server.py:40 get_recent_quality` | `tools.py:79 MesReadTools.get_recent_quality` | `batches JOIN inspections LEFT JOIN process_metrics`，按 line_id、started_at DESC、LIMIT（89-99） |
| `graph.py:166 alarm_data` | `mcp_client_tools.py:66 RemoteMesTools.get_recent_alerts` | `get_recent_alerts`（67） | `mcp_servers/mes_server.py:34 get_recent_alerts` | `tools.py:56 MesReadTools.get_recent_alerts` | `alerts WHERE device_id=? AND occurred_at>=? ORDER BY occurred_at DESC LIMIT ?`（69-75） |
| `graph.py:183 maintenance_data` | `mcp_client_tools.py:70 RemoteMesTools.get_maintenance_history` | `get_maintenance_history`（71） | `mcp_servers/mes_server.py:46 get_maintenance_history` | `tools.py:103 MesReadTools.get_maintenance_history` | `maintenance_records WHERE device_id=? ORDER BY completed_at DESC LIMIT ?`（113-119） |

参数校验：line 为 `^LINE-\d{2}$`，device 为 `^DEV-L\d{2}-LASER$`（tools.py:30-43）；告警 hours∈{8,24,72,168}、limit 1..50；质量 limit 3..30；维修 limit 1..20。图中用的是 24h/20 告警、20 条质量 JOIN 结果、10 条维修。

质量查询先 JOIN 再 LIMIT；当前不能无限推广为“一定返回20个不重复批次”，如果一个批次将来有多条 inspections，结果会重复批次。现有演示的表数据组织才支撑“20批次”文案。

四个 MES 查询都通过 `tools.py:123 _read_with_audit` 执行，finally 在 `tools.py:139` 写 `audit_logs`，actor=`DataAgent`、tool=`mes-mcp`、operation=业务方法名。参数验证失败发生在 `_read_with_audit` 之前，不会经过这个 finally。`ToolPolicy(timeout_seconds, max_attempts)` 仅被存到 self.policy（18-20、35），当前本地读取方法没有消费它来执行超时/重试。

### 5.3 Knowledge：MCP 名、审计名、检索实现不能混用

```text
graph.py:200 collect_knowledge_data
→ RemoteKnowledgeTools.search                         mcp_client_tools.py:76
→ caller.call("search_knowledge", {run_id,query,top_k}) mcp_client_tools.py:77
→ client.call_tool("search_knowledge", ...)
→ knowledge_server.search_knowledge                   mcp_servers/knowledge_server.py:29
→ tools.search                                       mcp_servers/knowledge_server.py:31
```

服务端 `tools` 在 `knowledge_server.py:13` 由 `milvus_knowledge.py:173 create_knowledge_tools` 选择：

1. lexical：`tools.py:156 KnowledgeTools.search` → `SELECT * FROM knowledge_docs`（160）→ `_keywords`（194）→ 固定关键词匹配与 tags 域加分（163-170）→ 降序取 top_k（171-180）。审计 operation 叫 `hybrid_search`（186），但这条实现没有向量数据库或标准 BM25。
2. milvus：`milvus_knowledge.py:128 MilvusKnowledgeTools.search` → DB 读 knowledge_docs（133）→ HashEmbeddingProvider.embed（21）→ `MilvusClient.search`（135，COSINE，候选数 `min(len(documents),top_k*3)`）→ lexical 归一化（148-154）→ `HybridReranker.rank`（155，方法在43）→ 固定 `0.65*dense+0.35*lexical`（39、57）。审计 operation=`milvus_hybrid_search`（157）。查询异常且 fail_open 时先记 degraded，再 fallback 到 lexical（162-170）。

Milvus 初始化还会读取 knowledge_docs，并 upsert 到向量集合（100-126）。HashEmbedding 是本地确定性哈希向量，不调用外部 embedding 模型；reranker 是固定权重融合，不是 CrossEncoder。初始化异常不在 search 的 fail_open try 内，不能说任意 Milvus 启动失败都能词法降级。

### 5.4 Action：不接受调用方通过网络传入审批对象

```text
graph.py:466 execute_actions
→ RemoteActionTools.create_maintenance_order              mcp_client_tools.py:82
→ caller.call("create_maintenance_order", {run_id,device_id,priority,title,description}) :83
→ client.call_tool("create_maintenance_order", ...)
→ action_server.create_maintenance_order                  mcp_servers/action_server.py:36
→ _server_verified_approval(run_id)                       action_server.py:27 / 调用点50
→ SafeActionTools.create_maintenance_order                tools.py:218
→ SELECT work_orders WHERE run_id=? AND device_id=?        tools.py:242
→ 已存在：审计 idempotent_replay 并返回旧 work_order_id     tools.py:246-261
→ 不存在：INSERT INTO work_orders                         tools.py:263-265
→ database.audit                                         tools.py:267
```

```text
graph.py:474 execute_actions
→ RemoteActionTools.tighten_inspection                    mcp_client_tools.py:84
→ caller.call("tighten_inspection", {run_id,line_id,interval_minutes}) :85
→ client.call_tool("tighten_inspection", ...)
→ action_server.tighten_inspection                        mcp_servers/action_server.py:55
→ _server_verified_approval(run_id)                       action_server.py:58
→ SafeActionTools.tighten_inspection                      tools.py:278
→ SELECT 当前 line_controls                              tools.py:291
→ SELECT 本 run 成功的 tighten_inspection audit_logs      tools.py:295-299
→ 已存在成功审计：返回 unchanged/idempotent               tools.py:300-316
→ 否则 UPDATE line_controls                              tools.py:317-319
→ database.audit                                         tools.py:327
```

`RemoteActionTools` 的两个方法签名都有 approval 参数，但构造远程 arguments 时刻意不上传它（83、85）。服务端 `_server_verified_approval` 通过 `database.get_run(run_id)` 读取解析后的 `run["approval"]`（action_server.py:28-29），只校验审批对象存在、approved 为真、approver 非空（30）。**此函数没有检查 approver 的角色，也没有检查 runs.status**；RBAC 是网关层负责。`get_run` 解析 approval_json 在 database.py:475-485。

SafeActionTools 再校验批准和审批人（227/285）以及参数范围。它没有依据 action_plan 对本次工具参数逐字段比对，也没有接收客户端提供的单独幂等键。工单幂等依据 run_id+device_id 先查后插；抽检幂等依据本 run 的成功审计先查后改。业务库没有工单 run_id+device_id 唯一约束；只有 id 主键，因此不能宣称数据库原子 exactly-once。

plan 中工具文案为 `action-mcp.create_maintenance_order` / `action-mcp.tighten_inspection`（graph.py:422、429），但实际 `call_tool` name 是**不带前缀**的函数名。graph.execute 不解释这些字符串，而是硬编码调用：P1、固定标题描述、抽检10分钟。标题还固定写“2号线”（graph.py:470），device_id/line_id 则来自 State。

### 5.5 inprocess 模式对应的区别

inprocess 下 `graph.self.mes` 直接是 `MesReadTools`，`self.actions` 直接是 `SafeActionTools`，knowledge 直接是 lexical/Milvus 实现。因此：

```text
Node → tools.py 或 milvus_knowledge.py → 数据库 / Milvus
```

这一模式没有 Remote adapter、`client.call_tool`、网络传输、MCP Server 函数、远程服务端审批再取数。SafeActionTools 只检查图传入的 approval 对象。日志仍然可能显示 `mes-mcp/knowledge-mcp/action-mcp`，这些审计标签本身不能证明执行了 MCP 协议调用。Neo4j 与 A2A 的行为开关独立于 `tool_transport`。

## 6. 不是 MCP 的节点：统计、Neo4j、A2A、RCA、验证和报告

### 6.1 确定性统计

`graph.py:223` → `production_analytics.py:44 analyze_production_window(quality, alarms)`。

先按 started_at 升序构造 trend（51-66），缺陷率=`100-yield_rate`。以阈值 97 分类成 baseline_rows（>=97）和 anomaly_rows（<97）（68-69），**不是**简单按前几批/后几批连续切分，也不是 changepoint 检测。

计算均值和 anomaly-baseline 的百分点差（71-86）；`_pearson:13` 对打码速度/功率/温度与缺陷率求 Pearson（89-99），每组至少 3 个有效配对，分母为 0 也返回 None。相关强度阈值为 |r|>=0.7 / >=0.4（32-37）。

对每个异常批次与每条告警计算绝对时间差，<=6小时算重合（101-117）。证据充分标准只有 `trend>=8 && baseline>=3 && anomaly>=2`（128）；“没有告警”会写 limitations，但不影响此布尔表达式。返回 `method, window_size, release_threshold, trend, baseline, anomaly, yield_change_pp, correlations, alarm_overlap, evidence_quality, recovery_validation`（130-155）。

graph 包装三条 evidence：STAT-WINDOW-1、STAT-CORR-1、STAT-TIME-1（235-269）。

### 6.2 Neo4j 图谱

```text
graph.py:303 search_fault_paths
→ neo4j_knowledge.py:172 Neo4jFaultKnowledgeTools.search_fault_paths
→ keywords(query)                                      neo4j_knowledge.py:156
→ driver.execute_query(Cypher, routing_=READ, ...)       neo4j_knowledge.py:182-205
→ Neo4j
→ record.data()                                        neo4j_knowledge.py:207
→ 业务库 audit_logs（tool=neo4j-graph, operation=traverse_fault_paths） :208
→ graph_context + GRAPH-* evidence                     graph.py:306-328
```

真实关系方向（查询在 neo4j_knowledge.py:184-187，初始化在145-149）：

```text
(DeviceModel)-[:HAS_COMPONENT]->(Component)
(FailureMode)-[:AFFECTS]->(Component)
(FailureMode)-[:INDICATED_BY]->(Symptom)
(FailureMode)-[:MITIGATED_BY]->(Remedy)-[:DOCUMENTED_IN]->(SOP)
```

这是基于设备型号的固定关系遍历，关键词匹配 component/failure/symptom 名称，按 matches DESC、path_id ASC 排序取 limit。不是模型生成 Cypher，也不经过 knowledge-mcp。

`neo4j_knowledge.py:96 __init__` 创建 GraphDatabase.driver 后 verify_connectivity、initialize；初始化会建立约束并 MERGE 六条 FAULT_PATHS。节点查询是只读，不能因此说整个组件从不写 Neo4j。禁用时 factory 返回 DisabledGraphKnowledgeTools（245-253）。运行时异常按 neo4j_fail_open 返回 []（218-230）；此时 graph 仍可能发 completed/0路径，区别于配置禁用时 skipped。

### 6.3 A2A 外部厂商专家

```text
graph.py:355 VendorExpertClient.consult(run_id, context)
→ a2a/client.py:66 consult
→ discover()                                           a2a/client.py:50
→ GET vendor_a2a_card_url                               a2a/client.py:53 / _request_json:35
→ vendor_server.agent_card                             a2a/vendor_server.py:139
→ 校验 protocolVersion=0.3.0、JSONRPC、endpoint同hostname a2a/client.py:54-62
→ POST card.url，JSON-RPC method="message/send"          a2a/client.py:71-84
→ vendor_server.a2a                                    a2a/vendor_server.py:171
→ 校验 bearer / JSON-RPC / role + messageId              vendor_server.py:172-196
→ messageId算uuid5 task_id；命中已有任务就直接返回        vendor_server.py:198-201
→ extract_context(message)                             vendor_server.py:72 / 202
→ diagnose(context)                                    vendor_server.py:84 / 203
→ store.put(task)                                      vendor_server.py:217
→ 返回 completed task + data artifact                  vendor_server.py:204-218
→ client取artifact里的data并增加agent_name/task_id/card   a2a/client.py:87-98
→ audit_logs（vendor-a2a, message/send）                  a2a/client.py:99
→ graph State vendor_expert + A2A-VENDOR-1               graph.py:365-392
```

context 字段为 `query, device_id, model, alarm_codes, production_analysis`（graph.py:348-354）。Client 使用稳定 messageId=`msg-{run_id}-vendor`（client.py:68），但 JSON-RPC request id 每次随机（73）。厂商 agent 当前 `diagnose` 是告警码+关键词匹配的普通 Python 函数，**不是另一轮 LLM**，也不会调用 MES 写工具。

VendorTaskStore 在 vendor_server.py:24：启用 Redis 时 Redis 存储 task、TTL=86400 秒（43-49），否则用进程字典（26、51）。服务端支持 tasks/get（184），但是当前调用链的 client 不轮询 tasks/get，只接受 message/send 直接返回 completed（client.py:88-89）。异常时 client 记审计，a2a_fail_open 为真则返回 None（110-122），graph 转为空 vendor_expert/skipped。

### 6.4 RCA 引擎

`graph.py:396` 调用统一协议 `RcaEngine.analyze`（analysis_engine.py:9-10）。

Demo：`analysis_engine.py:16 DemoRcaEngine.analyze` 把 evidence.excerpt 拼接，读取统计结果与证据 ID。只有出现“能量衰减”或“镜片”才加入镜片假设（31-48），然后固定追加“打码速度提升”和“视觉定位”（49-71），所以返回 **2或3个**假设，不总是3个。

LLM：`analysis_engine.py:78 OpenAIRcaEngine.__init__` 创建 `ChatOpenAI(temperature=0, model, api_key, base_url, timeout, max_retries)`（91-98）。`analyze:102` 内部定义 Hypothesis/Result Schema（105-112），把 query、production_analysis、evidence 拼入 prompt（114-123），然后：

```python
response = self.model.with_structured_output(
    Result, method="json_mode", include_raw=True
).invoke(prompt)  # analysis_engine.py:125-127
```

没有 bind_tools，没有 agent 工具调用循环。模型负责根因假设，所有数据库查询、工具选择、动作参数和审批流程在它之外固定编排。

confidence 的 0..1 是 Schema 约束（107）。“最多3个假设”只写在 prompt（117），Result.hypotheses 未设置 max_length、代码也未截断。成功后过滤不存在的 evidence_ids（147-154）；若全部无效，confidence 上限0.35（155-156）；加 model_source/degraded，usage 放首个假设（157-160）。若模型异常且 fail_open，则 Demo 规则回退并添加 deterministic-fallback/degraded/degraded_reason（137-145）。

### 6.5 verify：直接数据库读回 + 固定模拟窗口

下面是 `graph.py:487-490` 的原始关键调用：

```python
order = self.database.fetch_one(
    "SELECT * FROM work_orders WHERE run_id=? ORDER BY created_at DESC",
    (state["run_id"],),
)
control = self.database.fetch_one(
    "SELECT * FROM line_controls WHERE line_id=?", (state["line_id"],)
)
verification_batches = [96.8, 97.4, 97.9]
recovery = evaluate_recovery(verification_batches)
```

这里没有“查询工单 MCP 工具”，也没有调用 get_recent_quality 重采质量；直接 DB 读取不经过 MesReadTools._read_with_audit，因此没有该工具层审计。write_state_verified 只看 order 存在、control存在、抽检周期等于10（493），没有校验工单已完成或维修真实实施。

`production_analytics.py:158 evaluate_recovery` 从 yields 末尾反向数连续 >=97 的批次，遇到第一个不达标就 break（163-168）。当前 2 批连续达标，返回 status=observing、current_consecutive_batches=2、release_condition_met=False（169-176）。

verification 返回字段（graph.py:491-498）：`mode="simulation", write_state_verified, baseline_yield, verification_batches, release_condition_met, note`。其中名字叫 baseline_yield 的值取的是 **production_analysis.anomaly.average_yield**（494），不是稳定基线 average_yield。随后回填 production_analysis.recovery_validation（499-500）。

### 6.6 report

graph.py:504 generate_report 全部用 Python 字典组装：`report_id, generated_at, incident, scope, severity, primary_cause, evidence_count, approval, actions, verification, production_analysis, conclusion, data_notice`（506-520）。primary_cause 是 hypotheses[0]；不是额外模型总结。写 runs.report_json（521-524），return report（526）。最终 runs.status completed/rejected 由 service.resume_run 在 State 持久化后更新（service.py:193-198），不是 report 节点自己设置。

## 7. 审批暂停与恢复：必须讲清节点重入

首次到达 approval：

1. `graph.py:442` 写 runs.status=awaiting_approval。
2. `graph.py:443` 写 waiting 事件，key=`approval:waiting`。
3. `graph.py:444 interrupt({...})` 挂起，payload 包含 run_id/question/risk_level/actions；checkpoint 由编译注入的 SqliteSaver 持久化。
4. 图调用返回后 `service.py:124 _save_graph_state` 读取 checkpoint values 并写 runs.state_json（214-217）。

外部审批进来：

1. `service.py:140 approve` 先读 run，只接受 awaiting_approval（144）。构造 approval_id/approved/approver/comment（146-151），先把业务状态改成 executing（批准）或 running（拒绝）（153），**这一步尚未写 approval_json**。
2. 后续后台/队列调用 `service.py:156 resume_run`，先 claim 并检查状态（162-167），写 approval_decision 事件和 approval-gate 审计（168-184）。
3. 如 checkpoint 已有 values 且无 next，说明图已到终点，直接保存 State 并标终态返回（185-191）。
4. 其他情况执行 `self.graph.invoke(Command(resume=approval), config=self.config(run_id))`（192），配置中 `thread_id=run_id`（88-89）。
5. **恢复 approval 节点时会从该节点函数开头重入，不是跳到 interrupt 后面。** 所以442/443再次执行；444的 interrupt 在这次运行中取得 resume 提供的决定后返回。原先完成的采集/统计/RCA节点不因此从头重跑。
6. waiting事件重复执行不等于重复插入：graph._event 的key固定；database.py:439 key、446 INSERT OR IGNORE，schema 有 UNIQUE(run_id,idempotency_key)（database.py:133，MySQL为mysql_database.py:74）。但442的业务状态更新确实会重新执行。
7. 452-458补上 decided_at，459写 runs.approval_json，然后460返回 Command 同时更新 State.approval 并跳 execute/reject。**这时 action-mcp 才能从业务库读到批准记录。**
8. execute 开头463再次把业务状态改成 executing；批准链 execute→verify→report，拒绝链 reject→report。图跑完后 service.py:193先保存 State，196才发 completed/rejected 终态，防止 SSE看到终态而业务State还是旧值。

恢复和副作用的边界：节点可能重入，checkpoint不承诺每个外部副作用exactly-once；事件靠唯一键，动作靠业务幂等检查。service.approve 的“检查后更新”本身不是数据库compare-and-set，不能把注释“prevent double approval”解读为严格原子并发保证。

## 8. 现有手册需要修正/补足的地方

`docs/build_code_walkthrough_handbook.py` 是当前代码链路手册生成源，已经准确写了 verify直读DB/固定三批（467、666），也说明工单缺少唯一约束（660）。不要把这些已正确内容再次判成错误。

仍应修正/补全：

| 现有位置 | 说法或遗漏 | 应改成的源码事实 |
| --- | --- | --- |
| `build_code_walkthrough_handbook.py:509` | events表 | 实际表名run_events |
| `build_code_walkthrough_handbook.py:582` | Demo规则生成3个假设 | 条件分支导致2或3个；镜片条件满足才3个 |
| `build_code_walkthrough_handbook.py:525` | execute输入“approval、计划范围” | execute不消费action_plan，硬编码调用两个业务函数 |
| `build_code_walkthrough_handbook.py:602` | 只写“approval节点恢复后…” | 补充从函数开头重入、interrupt之前副作用重复发生、waiting事件的唯一键去重 |
| `build_code_walkthrough_handbook.py:830` | production_analytics.py:46 | analyze_production_window现在从44行定义开始 |
| `INTERVIEW_QA.md:98` | 拒绝后似乎也会verify | 实际reject直接report，跳过verify |
| `INTERVIEW_QA.md:160` | State包括当前步骤和错误，建议Pydantic | 实际AgentState为TypedDict，无current_step/error/status字段 |
| `INTERVIEW_QA.md:218` | 执行后重新查询质量数据 | 当前只DB读回work_orders/line_controls；三批质量固定列表 |
| `INTERVIEW_QA.md:224` | 证据不足后计划优先补采/人工复核 | 当前plan始终固定两个写动作，未根据evidence_quality改计划 |
| `INTERVIEW_QA.md:380` | Action MCP检查审批角色和任务状态 | 当前只检查持久化approval存在、approved、approver；角色在网关校验 |
| `INTERVIEW_QA.md:166,386` | 动作数据库唯一约束、请求携带幂等键 | 工单只有主键与普通run索引，业务是先查后写；实际调用参数没有独立幂等键 |
| `INTERVIEW_QA.md:142` | 未来增加持久化队列 | 与当前已存在Celery实现不一致；应区分已有队列与尚无启动扫描恢复器 |

额外表达边界：不要把审计operation=`hybrid_search`当作MCP call_tool name（后者search_knowledge）；不要因日志里出现`*-mcp`断定inprocess也走网络；不要把“15节点”说成“15次LLM/15个独立Agent”；不要把Neo4j/A2A画进knowledge-mcp；不要把报告completed当作质量passed；当前图只在RCA可选调用一次模型，生产验证没有在线采样循环。
