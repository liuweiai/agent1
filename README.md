# FactoryPilot

FactoryPilot 是一个面向 MES 场景的“生产异常诊断与闭环处置 Agent”。它会并行查询生产、质量、告警、维修和知识数据，形成带引用的根因假设；高风险写操作在 LangGraph checkpoint 处暂停，只有具备权限的人员审批后，才会通过受控 MCP 工具执行并生成事故报告。

全部业务数据均为确定性合成数据，不包含实习公司数据。

## 已实现能力

- LangGraph 并行证据收集、根因分析、风险计划、`interrupt` 审批、执行、验证和事故报告。
- SQLite 本地模式与 MySQL 8 生产化数据模式；所有查询参数化，Agent 没有任意 SQL 执行能力。
- 10 台设备、1000+ 告警、500 个批次、100+ 维修记录、30 份手册/SOP。
- Milvus 向量检索与关键词分数融合重排，结果包含文档、章节、版本和分数；故障时可配置降级到本地检索。
- `mes-mcp`、`knowledge-mcp`、`action-mcp` 三个真实 MCP v2 Streamable HTTP Server。
- MCP Client 超时、有限重试、结构化输出解析；所有工具调用写入审计日志。
- Spring Boot WebFlux 网关、JWT HttpOnly Cookie、RBAC；只有 `SUPERVISOR`/`ADMIN` 可审批。
- 网关向 Agent Service 注入共享密钥，生产模式禁止绕过网关直接访问业务 API。
- SQLite checkpoint 持久化；服务重启后可按同一 `thread_id` 恢复人工审批任务。
- React + TypeScript 操作台展示执行时间线、良率、证据链、审批方案与报告。
- pytest、MCP 协议测试、Spring Security 集成测试、前端构建、30 条本地回归集和 LangSmith 在线评测入口。

## 架构与工作流

```text
React :3000
  └─ Spring Gateway :8080  JWT / RBAC / SSE proxy
       └─ FastAPI Agent :8000  LangGraph / checkpoint
            ├─ mes-mcp :8101 ───────────────┐
            ├─ knowledge-mcp :8102 ─ Milvus │─ MySQL
            └─ action-mcp :8103 ────────────┘
```

```mermaid
flowchart TD
    U[生产异常] --> T[TriageAgent]
    T --> M[MES / 质量]
    T --> A[设备告警]
    T --> H[维修记录]
    T --> K[Milvus SOP 检索]
    M --> R[RCAAgent]
    A --> R
    H --> R
    K --> R
    R --> C[证据与置信度检查]
    C --> P[ActionAgent 处置计划]
    P --> I{LangGraph interrupt}
    I -->|拒绝| X[零写操作安全终止]
    I -->|主管批准| E[action-mcp 受控写入]
    E --> V[结果验证]
    V --> O[事故报告]
```

## 推荐启动：完整 Docker 环境

要求 Docker Desktop 已启动。首次拉取 MySQL、Milvus、MinIO 等镜像会花一些时间。

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

打开 <http://localhost:3000>，使用演示账号登录：

| 用户名 | 密码 | 角色 |
| --- | --- | --- |
| `operator` | `operator123` | 只读和发起诊断 |
| `supervisor` | `supervisor123` | 发起诊断、批准/拒绝 |
| `admin` | `admin123` | 管理员演示角色 |

常用地址：

- React：<http://localhost:3000>
- Gateway：<http://localhost:8080/actuator/health>
- Agent 健康检查：<http://localhost:8001/health>
- Swagger：<http://localhost:8001/docs>（生产模式下业务 API 仍需要网关密钥）
- MCP：`http://localhost:8101/mcp`、`8102/mcp`、`8103/mcp`
- Milvus：`localhost:19531`（避免占用本机已有的 `19530`）
- MySQL：`localhost:3307`（避免占用本机已有的 `3306`）

查看日志或停止服务：

```powershell
docker compose logs -f agent-service mes-gateway knowledge-mcp
docker compose stop
```

`stop` 不会删除 MySQL、Milvus 或 checkpoint 数据。只有明确需要清空演示数据时才使用 `docker compose down -v`。

## 轻量本地开发模式

本地模式使用 SQLite、进程内安全工具和词法检索，不依赖 MySQL/Milvus/MCP Server，但前端仍通过带身份认证的 Java 网关访问。
建议使用 Python 3.11+、Node.js 18+ 和 Java 8+。

终端 1：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\agent-service[dev]"
.\.venv\Scripts\python.exe -m uvicorn factorypilot.main:app --app-dir agent-service --reload --port 8000
```

终端 2：

```powershell
cd mes-gateway
mvn spring-boot:run
```

终端 3：

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173>。首次启动会创建 `data/factorypilot.db` 与 `data/checkpoints.db`。

## 配置生产化组件

关键环境变量见 [.env.example](.env.example)：

| 配置 | 可选值/示例 | 用途 |
| --- | --- | --- |
| `DATA_BACKEND` | `sqlite` / `mysql` | 业务与审计数据 |
| `DATABASE_URL` | `mysql://...` | MySQL 连接 |
| `KNOWLEDGE_BACKEND` | `lexical` / `milvus` | 知识检索后端 |
| `MILVUS_URI` | `http://milvus:19530` | Milvus 地址 |
| `TOOL_TRANSPORT` | `inprocess` / `mcp` | 编排器工具传输 |
| `REQUIRE_GATEWAY_AUTH` | `true` | 禁止直连业务 API |
| `GATEWAY_SHARED_SECRET` | 随机长字符串 | 网关和 Agent 间认证 |
| `JWT_SECRET` | 至少 32 字节 | JWT 签名密钥 |
| `LANGSMITH_TRACING` | `true` | 开启链路追踪 |
| `LANGSMITH_WORKSPACE_ID` | 工作区 UUID | 多工作区 Key 的目标工作区 |
| `LANGSMITH_ENDPOINT` | US/EU/AWS API 地址 | LangSmith 区域端点 |

Docker Compose 默认使用 `mysql + milvus + mcp + gateway auth` 完整组合。部署前必须替换 `.env` 中的共享密钥和 JWT 密钥。

## API 与权限

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/auth/login` | 公开 | 登录并写入 HttpOnly Cookie |
| `GET` | `/api/auth/me` | 已登录 | 当前身份与角色 |
| `POST` | `/api/runs` | 已登录 | 创建诊断任务 |
| `GET` | `/api/runs/{id}` | 已登录 | 状态、证据、审批与报告 |
| `GET` | `/api/runs/{id}/events` | 已登录 | SSE 执行时间线 |
| `POST` | `/api/runs/{id}/approval` | 主管/管理员 | 审批并恢复 checkpoint |

审批人由网关根据 JWT 身份写入，客户端提交的 `approver` 不会被信任。`action-mcp` 还会再次读取服务端审批记录，并校验设备、产线、枚举、参数范围和单次执行上限。

## 测试与评测

```powershell
.\.venv\Scripts\python.exe -m pytest agent-service\tests -q
.\.venv\Scripts\python.exe evals\evaluate.py
cd frontend
npm run build
cd ..\mes-gateway
mvn test
```

本地评测报告见 [evals/report.md](evals/report.md)。数据为确定性合成数据，报告中的高分只用于验证工作流回归，不能代表真实工厂数据上的模型泛化能力。

LangSmith 在线数据集与回归实验：

```powershell
$env:LANGSMITH_API_KEY = "你的 Key"
$env:LANGSMITH_PROJECT = "factorypilot"
# 多工作区账号按控制台 Settings > General 补充：
# $env:LANGSMITH_WORKSPACE_ID = "你的 Workspace ID"
.\.venv\Scripts\python.exe evals\langsmith_eval.py
```

脚本会同步 `factorypilot-regression-v1` 数据集并运行意图准确率、`Recall@4` 和引用完整性评测。没有 Key 时可继续使用完全离线的 `evals/evaluate.py`。

## 可选真实 LLM

默认 `AGENT_MODE=demo`，无需模型 Key 也能演示完整闭环。需要真实模型时：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".\agent-service[llm]"
$env:AGENT_MODE = "llm"
$env:OPENAI_API_KEY = "..."
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:OPENAI_MODEL = "deepseek-v4-flash"
```

Docker 模式在根目录 `.env` 中使用相同变量，然后重建 Agent：

```powershell
docker compose up -d --build --force-recreate agent-service
```

`OPENAI_*` 是 OpenAI 兼容协议配置名，可用于 DeepSeek；Key 只保存在 `.env`，不要提交到 Git。

默认 `LLM_FAIL_OPEN=true`。当模型服务超时或网络不可达时，RCA 节点会降级到确定性证据规则，并在时间线与假设元数据中标记 `degraded=true`，不会把降级结果伪装成模型结果。Docker 需要代理时可在 `.env` 配置 `HTTP_PROXY/HTTPS_PROXY`，并保留模板中的内部服务 `NO_PROXY` 列表。

OpenAI 官方模型示例：

```powershell
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "gpt-4.1-mini"
```

结构化 RCA 输出只能引用已收集的 evidence id。建议开启 LangSmith tracing 后对模型、Prompt 和检索版本分别建立回归实验。

## 安全边界

1. MES MCP 只暴露固定只读方法，服务端持有参数化 SQL。
2. LangGraph 在所有写副作用之前持久化暂停。
3. 网关 RBAC 和 action MCP 审批复核形成双层授权。
4. 写工具只允许创建模拟维修工单和把抽检周期设置为 `5/10/15/30` 分钟。
5. 工具调用记录 actor、参数、结果、耗时；事件使用幂等键防止恢复时重复写入。
6. MCP 网络调用有超时、有限重试和最大工作流循环限制；Milvus 可按配置安全降级。

## 后续可扩展项

- 使用企业 IdP/OIDC 替换内存演示账号，并将刷新令牌与用户管理落库。
- 使用正式 Embedding 服务和 Milvus BM25 sparse field 替换无 Key 的确定性演示向量。
- 为 MCP 增加服务级 OAuth/mTLS、熔断器和指标面板。
- 增加独立部署的设备厂商专家 A2A Agent；内部 LangGraph 节点继续直接编排。
- 在真实脱敏 MES 样本上建立人工标注集，补充 LLM-as-judge 和故障注入评测。

## 参考

- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Milvus 文档](https://milvus.io/docs/)
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation-quickstart)
- [Spring Security Reactive JWT](https://docs.spring.io/spring-security/reference/reactive/oauth2/resource-server/jwt.html)
