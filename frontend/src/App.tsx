import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Input,
  Layout,
  List,
  message,
  Modal,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  AuditOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  PlayCircleFilled,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltFilled,
  WarningFilled,
  UserOutlined,
  LogoutOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'

import { api } from './api'
import { cursorFromEvents, subscribeRunEvents } from './runEvents'
import type {
  AuditLog,
  AuthUser,
  EvaluationReport,
  Evidence,
  RunDetail,
  RunStatus,
  RunSummary,
  TimelineEvent,
} from './types'

const { Header, Content } = Layout
const { Text, Title, Paragraph } = Typography
const { TextArea } = Input

const DEFAULT_QUERY = '2号产线激光打码设备良率从 98% 降到 91%，帮我分析原因并给出处置方案。'
const AUTH_ENABLED = (import.meta.env.VITE_AUTH_ENABLED ?? 'true') === 'true'

type WorkspaceView = 'console' | 'tasks' | 'evaluation' | 'audit'

const VIEW_CONTEXT: Record<WorkspaceView, { breadcrumb: string; title: string }> = {
  console: { breadcrumb: '生产运营 / 异常处置', title: '异常诊断' },
  tasks: { breadcrumb: '生产运营 / 任务管理', title: '异常任务' },
  evaluation: { breadcrumb: 'Agent 工程 / 质量保障', title: '评测中心' },
  audit: { breadcrumb: '安全治理 / 工具调用', title: '审计日志' },
}

const STATUS: Record<RunStatus, { text: string; color: string }> = {
  queued: { text: '排队中', color: 'default' },
  running: { text: '分析中', color: 'processing' },
  awaiting_approval: { text: '等待审批', color: 'warning' },
  executing: { text: '执行中', color: 'processing' },
  completed: { text: '已完成', color: 'success' },
  rejected: { text: '已拒绝', color: 'default' },
  failed: { text: '执行失败', color: 'error' },
}

const EVENT_COLOR: Record<TimelineEvent['status'], string> = {
  completed: '#31d6a7',
  running: '#44a4ff',
  waiting: '#ffb84d',
  failed: '#ff5c7c',
  skipped: '#70829a',
}

function App() {
  const [activeView, setActiveView] = useState<WorkspaceView>('console')
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [run, setRun] = useState<RunDetail | null>(null)
  const [history, setHistory] = useState<RunSummary[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approver, setApprover] = useState('生产主管-刘工')
  const [comment, setComment] = useState('批准模拟处置，执行后继续加严检验。')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginUsername, setLoginUsername] = useState('supervisor')
  const [loginPassword, setLoginPassword] = useState('supervisor123')
  const [loggingIn, setLoggingIn] = useState(false)
  const eventSource = useRef<{ close: () => void } | null>(null)
  const selectedRun = useRef<string | null>(null)

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.listRuns())
    } catch {
      // The main panel surfaces connectivity failures; history is non-critical.
    }
  }, [])

  const refreshRun = useCallback(async (id: string) => {
    const detail = await api.getRun(id)
    if (selectedRun.current === id) setRun(detail)
    return detail
  }, [])

  const connectEvents = useCallback(
    (id: string, initialCursor = 0) => {
      eventSource.current?.close()
      eventSource.current = subscribeRunEvents(id, {
        url: (after) => api.eventUrl(id, after),
        initialCursor,
        refresh: () => refreshRun(id),
        onDone: () => { void refreshHistory() },
      })
    },
    [refreshHistory, refreshRun],
  )

  useEffect(() => {
    if (AUTH_ENABLED) {
      api.me()
        .then((identity) => {
          setUser(identity)
          refreshHistory()
        })
        .catch(() => setLoginOpen(true))
    } else {
      refreshHistory()
    }
    return () => eventSource.current?.close()
  }, [refreshHistory])

  const login = async () => {
    setLoggingIn(true)
    try {
      const identity = await api.login(loginUsername, loginPassword)
      setUser(identity)
      setLoginOpen(false)
      await refreshHistory()
      message.success(`欢迎，${identity.display_name}`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '登录失败')
    } finally {
      setLoggingIn(false)
    }
  }

  const logout = async () => {
    eventSource.current?.close()
    selectedRun.current = null
    await api.logout().catch(() => undefined)
    setUser(null)
    setRun(null)
    setLoginOpen(true)
  }

  const startDiagnosis = async () => {
    if (query.trim().length < 5) return
    setCreating(true)
    try {
      const created = await api.createRun(query.trim())
      eventSource.current?.close()
      selectedRun.current = created.id
      const detail = await refreshRun(created.id)
      if (selectedRun.current === created.id) connectEvents(created.id, cursorFromEvents(detail.events))
      refreshHistory()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '任务创建失败')
    } finally {
      setCreating(false)
    }
  }

  const openRun = async (id: string) => {
    eventSource.current?.close()
    selectedRun.current = id
    setActiveView('console')
    setLoading(true)
    try {
      const detail = await refreshRun(id)
      if (selectedRun.current === id && !['completed', 'rejected', 'failed'].includes(detail.status)) {
        connectEvents(id, cursorFromEvents(detail.events))
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  const decide = async (approved: boolean) => {
    if (!run) return
    try {
      await api.approve(run.id, approved, user?.username ?? approver, comment)
      setApprovalOpen(false)
      const detail = await refreshRun(run.id)
      if (selectedRun.current === run.id) connectEvents(run.id, cursorFromEvents(detail.events))
      message.success(approved ? '审批已提交，开始执行受控工具' : '已拒绝，写操作不会执行')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '审批失败')
    }
  }

  const evidence = run?.state.evidence ?? []
  const hypotheses = run?.state.hypotheses ?? []
  const topCause = hypotheses[0]
  const productionAnalysis = run?.state.production_analysis
  const canApprove = !AUTH_ENABLED || Boolean(user?.roles.some((role) => ['SUPERVISOR', 'ADMIN'].includes(role)))

  const chartOption = useMemo(() => {
    const historic = [...(run?.state.quality ?? [])].reverse()
    const verification = run?.state.verification?.verification_batches ?? []
    const labels = [
      ...historic.map((item) => item.batch_id.replace('BATCH-', 'B')),
      ...verification.map((_, index) => `验证${index + 1}`),
    ]
    const values = [...historic.map((item) => item.yield_rate), ...verification]
    return {
      backgroundColor: 'transparent',
      grid: { left: 42, right: 18, top: 34, bottom: 34 },
      tooltip: { trigger: 'axis', valueFormatter: (value: number) => `${value}%` },
      xAxis: {
        type: 'category',
        data: labels,
        axisLabel: { color: '#71839b', fontSize: 10, interval: Math.max(0, Math.floor(labels.length / 7)) },
        axisLine: { lineStyle: { color: '#22344b' } },
      },
      yAxis: {
        type: 'value',
        min: 88,
        max: 100,
        axisLabel: { color: '#71839b', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#17283d' } },
      },
      series: [
        {
          data: values,
          type: 'line',
          smooth: true,
          symbolSize: 7,
          lineStyle: { color: '#3b82f6', width: 2 },
          itemStyle: { color: '#3b82f6', borderColor: '#111827', borderWidth: 2 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(59,130,246,.18)' },
                { offset: 1, color: 'rgba(59,130,246,0)' },
              ],
            },
          },
          markLine: {
            symbol: 'none',
            label: { color: '#ffb84d', formatter: '放行线 97%' },
            lineStyle: { color: '#ffb84d', type: 'dashed' },
            data: [{ yAxis: 97 }],
          },
        },
      ],
    }
  }, [run])

  return (
    <Layout className="app-shell">
      <aside className="app-sidebar">
        <div className="brand">
          <div className="brand-mark"><ThunderboltFilled /></div>
          <div>
            <div className="brand-name">FactoryPilot</div>
            <div className="brand-caption">MES 异常闭环控制台</div>
          </div>
        </div>

        <div className="sidebar-section-label">系统导航</div>
        <nav className="sidebar-nav" aria-label="主要导航">
          <button type="button" className={`sidebar-nav-item ${activeView === 'console' ? 'active' : ''}`} onClick={() => setActiveView('console')}><RobotOutlined /><span>异常控制台</span></button>
          <button type="button" className={`sidebar-nav-item ${activeView === 'tasks' ? 'active' : ''}`} onClick={() => setActiveView('tasks')}><ClockCircleOutlined /><span>异常任务</span><span className="nav-count">{history.length}</span></button>
          <button type="button" className={`sidebar-nav-item ${activeView === 'evaluation' ? 'active' : ''}`} onClick={() => setActiveView('evaluation')}><AuditOutlined /><span>评测中心</span></button>
          <button type="button" className={`sidebar-nav-item ${activeView === 'audit' ? 'active' : ''}`} onClick={() => setActiveView('audit')}><SafetyCertificateOutlined /><span>审计日志</span></button>
        </nav>

        <div className="sidebar-section-label">服务状态</div>
        <div className="environment-card">
          <div><span className="environment-dot" />模拟环境</div>
          <span>MySQL · Redis · Milvus · Neo4j</span>
          <span>Celery · MCP · A2A · checkpoint</span>
        </div>

        <div className="sidebar-spacer" />
        {user && (
          <div className="sidebar-operator">
            <div className="operator-avatar"><UserOutlined /></div>
            <div>
              <strong>{user.roles.includes('SUPERVISOR') ? '生产主管-刘工' : user.display_name}</strong>
              <span>{user.roles.includes('SUPERVISOR') ? 'SUPERVISOR' : 'OPERATOR'}</span>
            </div>
          </div>
        )}
      </aside>

      <Layout className="app-workspace">
      <Header className="app-header">
        <div className="header-context">
          <span>{VIEW_CONTEXT[activeView].breadcrumb}</span>
          <strong>{VIEW_CONTEXT[activeView].title}</strong>
        </div>
        <Space size="middle" className="header-actions">
          <Badge status="processing" text={<span className="muted">Agent Service 在线</span>} />
          {user && (
            <Space className="identity">
              <Tag>{user.roles.includes('SUPERVISOR') ? '主管' : '操作员'}</Tag>
              <Button type="text" size="small" icon={<LogoutOutlined />} onClick={logout} />
            </Space>
          )}
          <Tag className="simulation-tag">模拟环境</Tag>
        </Space>
      </Header>

      <Content className="app-content">
        {activeView === 'console' && <>
        <section className="command-panel">
          <div className="command-intro">
            <Title level={2}>新建诊断任务</Title>
            <Paragraph className="lead">输入设备、产线或质量异常。系统将查询 MES 数据、执行统计分析，并根据 SOP 生成带证据的处置建议。</Paragraph>
          </div>
          <div className="query-box">
            <div className="query-field">
              <div className="query-label-row">
                <span className="query-label">异常描述</span>
                <span className="query-context">最多 500 字</span>
              </div>
              <TextArea value={query} onChange={(event) => setQuery(event.target.value)} autoSize={{ minRows: 2, maxRows: 4 }} maxLength={500} />
            </div>
            <Button type="primary" size="large" icon={<PlayCircleFilled />} loading={creating} onClick={startDiagnosis}>
              启动诊断
            </Button>
          </div>
        </section>

        <Card className="surface recent-runs-card" bordered={false}>
          <div className="recent-runs-header">
            <div>
              <ClockCircleOutlined />
              <strong>最近任务</strong>
              <Text type="secondary">最近 4 条诊断记录</Text>
            </div>
            <Button type="text" size="small" onClick={() => setActiveView('tasks')}>查看全部 {history.length} 条 →</Button>
          </div>
          {history.length > 0 ? (
            <div className="recent-runs-grid">
              {history.slice(0, 4).map((item) => (
                <button className={`recent-run-item ${run?.id === item.id ? 'active' : ''}`} key={item.id} onClick={() => openRun(item.id)}>
                  <div className="recent-run-top">
                    <span>{item.line_id}</span>
                    <Tag color={STATUS[item.status].color}>{STATUS[item.status].text}</Tag>
                  </div>
                  <strong>{item.query}</strong>
                  <small>{new Date(item.created_at).toLocaleString('zh-CN')}</small>
                </button>
              ))}
            </div>
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />}
        </Card>

        <Spin spinning={loading}>
              {!run ? (
                <Card className="surface empty-stage" bordered={false}>
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      <div className="empty-copy">
                        <strong>暂无选中的诊断任务</strong>
                        <span>可从最近任务中选择一条记录，或在上方创建新任务。</span>
                      </div>
                    }
                  />
                </Card>
              ) : (
                <Space direction="vertical" size={20} style={{ width: '100%' }}>
                  <Card className="surface incident-card" bordered={false}>
                    <div className="incident-header">
                      <div>
                        <Space>
                          <Tag color={STATUS[run.status].color}>{STATUS[run.status].text}</Tag>
                          <Text className="run-id">RUN {run.id.slice(0, 8).toUpperCase()}</Text>
                        </Space>
                        <Title level={3}>{run.query}</Title>
                      </div>
                      <Space size="large" className="incident-meta">
                        <Statistic title="产线" value={run.line_id} prefix={<DatabaseOutlined />} />
                        <Statistic title="严重度" value={run.severity ?? '--'} valueStyle={{ color: '#ff728b' }} prefix={<WarningFilled />} />
                        <Statistic title="证据" value={evidence.length} suffix="条" prefix={<FileSearchOutlined />} />
                      </Space>
                    </div>
                    {run.status === 'awaiting_approval' && (
                      <Alert
                        className="approval-alert"
                        type="warning"
                        showIcon
                        icon={<SafetyCertificateOutlined />}
                        message="高风险写操作已暂停"
                        description="LangGraph checkpoint 已保存。审批前不会创建工单或修改模拟 MES 参数。"
                        action={canApprove
                          ? <Button type="primary" danger onClick={() => setApprovalOpen(true)}>进入审批</Button>
                          : <Button disabled>需要主管权限</Button>}
                      />
                    )}
                  </Card>

                  <div className="metric-strip">
                    <div className="metric-card danger">
                      <span>当前良率</span>
                      <strong>{productionAnalysis?.anomaly.average_yield ?? run.state.quality?.[0]?.yield_rate ?? '--'}%</strong>
                      <small>{productionAnalysis?.yield_change_pp != null ? `${productionAnalysis.yield_change_pp.toFixed(2)} pp` : '等待质量数据'}</small>
                    </div>
                    <div className="metric-card warning">
                      <span>风险等级</span>
                      <strong>{run.severity ?? '--'}</strong>
                      <small>{run.status === 'awaiting_approval' ? '需要人工审批' : STATUS[run.status].text}</small>
                    </div>
                    <div className="metric-card success">
                      <span>证据覆盖</span>
                      <strong>{evidence.length} 条</strong>
                      <small>{new Set(evidence.map((item) => item.source_type)).size} 类数据源</small>
                    </div>
                    <div className="metric-card info">
                      <span>工作流进度</span>
                      <strong>{run.events.filter((event) => event.status === 'completed').length} / {run.events.length}</strong>
                      <small>{run.events.some((event) => event.status === 'running') ? 'Agent 正在执行' : STATUS[run.status].text}</small>
                    </div>
                  </div>

                  <div className="analysis-grid">
                      <Card className="surface timeline-card" bordered={false} title={<span><RobotOutlined /> Agent 执行工作流</span>} extra={<Text type="secondary">实时审计</Text>}>
                        <Timeline>
                          {run.events.map((event) => (
                            <Timeline.Item color={EVENT_COLOR[event.status]} key={event.id}>
                              <div className="event-title">
                                {event.title}
                                {event.status === 'running' && <span className="pulse-dot" />}
                              </div>
                              <div className="event-detail">{event.detail}</div>
                              <div className="event-time">{new Date(event.created_at).toLocaleTimeString()}</div>
                            </Timeline.Item>
                          ))}
                        </Timeline>
                      </Card>
                      <Space className="analysis-primary-stack" direction="vertical" size={20} style={{ width: '100%' }}>
                        <Card className="surface chart-card" bordered={false} title="最近 20 批次良率趋势" extra={<Tag color="blue">程序计算</Tag>}>
                          {(run.state.quality?.length ?? 0) > 0 ? <ReactECharts option={chartOption} style={{ height: 280 }} /> : <Empty description="等待质量数据" />}
                          {run.state.verification && <Alert type={run.state.verification.write_state_verified === false ? 'error' : 'info'} showIcon message={run.state.verification.note} />}
                        </Card>

                        {productionAnalysis && (
                          <Card className="surface analytics-card" bordered={false} title="确定性生产统计证据">
                            <Row gutter={[12, 12]}>
                              <Col xs={12} md={6}>
                                <Statistic title={`稳定基线 · ${productionAnalysis.baseline.count}批`} value={productionAnalysis.baseline.average_yield ?? '--'} suffix="%" precision={2} />
                              </Col>
                              <Col xs={12} md={6}>
                                <Statistic title={`异常窗口 · ${productionAnalysis.anomaly.count}批`} value={productionAnalysis.anomaly.average_yield ?? '--'} suffix="%" precision={2} valueStyle={{ color: '#ff728b' }} />
                              </Col>
                              <Col xs={12} md={6}>
                                <Statistic title="良率下降" value={Math.abs(productionAnalysis.yield_change_pp ?? 0)} suffix="pp" precision={2} valueStyle={{ color: '#ffb84d' }} />
                              </Col>
                              <Col xs={12} md={6}>
                                <Statistic title={`告警重合 · ±${productionAnalysis.alarm_overlap.window_hours}h`} value={productionAnalysis.alarm_overlap.overlap ? '是' : '否'} suffix={productionAnalysis.alarm_overlap.overlap ? `${productionAnalysis.alarm_overlap.matched_alarm_count}条` : ''} />
                              </Col>
                            </Row>
                            <Divider />
                            <div className="correlation-grid">
                              {Object.values(productionAnalysis.correlations).map((item) => (
                                <div className="correlation-item" key={item.label}>
                                  <Text type="secondary">{item.label}</Text>
                                  <Space>
                                    <Text strong>r = {item.coefficient ?? 'N/A'}</Text>
                                    <Tag color={item.coefficient != null && Math.abs(item.coefficient) >= 0.7 ? 'green' : 'blue'}>{item.interpretation}</Tag>
                                  </Space>
                                </div>
                              ))}
                            </div>
                            <Alert
                              className="analytics-alert"
                              type={productionAnalysis.evidence_quality.sufficient ? 'success' : 'warning'}
                              showIcon
                              message={productionAnalysis.evidence_quality.sufficient ? '统计证据充分，可交给 RCA Agent 进行因果假设' : '统计证据不足，需要人工补充数据'}
                              description={productionAnalysis.evidence_quality.limitations.join('；') || `方法：${productionAnalysis.method}。LLM 只能引用这些计算结果，不能自行修改统计结论。`}
                            />
                            <Alert
                              className="analytics-alert"
                              type={run.state.verification?.write_state_verified === false ? 'error' : productionAnalysis.recovery_validation.status === 'passed' ? 'success' : 'info'}
                              showIcon
                              message={run.state.verification?.write_state_verified === false
                                ? '写入验证失败，暂停质量放行判断'
                                : productionAnalysis.recovery_validation.status === 'pending'
                                ? '处置后连续三批恢复验证：等待执行'
                                : `连续达标 ${productionAnalysis.recovery_validation.current_consecutive_batches ?? 0}/${productionAnalysis.recovery_validation.required_consecutive_batches} 批`}
                            />
                          </Card>
                        )}

                        {topCause && (
                          <Card className="surface cause-card" bordered={false} title="首要根因假设">
                            <div className="cause-top">
                              <div>
                                <Title level={4}>{topCause.cause}</Title>
                              </div>
                              <Progress type="circle" width={68} percent={Math.round(topCause.confidence * 100)} strokeColor="#3b82f6" trailColor="#263241" />
                            </div>
                            <Paragraph>{topCause.reasoning}</Paragraph>
                            <Space wrap>{topCause.evidence_ids.map((id) => <Tag key={id}>{id}</Tag>)}</Space>
                          </Card>
                        )}
                      </Space>
                  </div>

                  {hypotheses.length > 0 && (
                    <Card className="surface" bordered={false} title={<span><AuditOutlined /> 根因候选与置信度</span>}>
                      <Row gutter={[16, 16]}>
                        {hypotheses.map((item, index) => (
                          <Col xs={24} md={8} key={item.cause}>
                            <div className="hypothesis-card">
                              <div className="hypothesis-rank">0{index + 1}</div>
                              <Title level={5}>{item.cause}</Title>
                              <Progress percent={Math.round(item.confidence * 100)} showInfo={false} strokeColor={index === 0 ? '#31d6a7' : '#44a4ff'} trailColor="#1b2c42" />
                              <Text type="secondary">置信度 {Math.round(item.confidence * 100)}%</Text>
                              <Paragraph>{item.reasoning}</Paragraph>
                            </div>
                          </Col>
                        ))}
                      </Row>
                    </Card>
                  )}

                  {evidence.length > 0 && <EvidencePanel evidence={evidence} />}

                  {run.report && (
                    <Card className="surface report-card" bordered={false} title={<span>{run.report.outcome === 'failed' ? <WarningFilled /> : <CheckCircleFilled />} 事故报告 {run.report.report_id}</span>}>
                      {run.state.verification?.write_state_verified === false && (
                        <Alert type="error" showIcon message="写入验证失败，需人工核查"
                          description={<>{run.state.verification.note}<br />{run.state.verification.next_steps?.join(' ')}</>} />
                      )}
                      <Descriptions column={{ xs: 1, md: 3 }}>
                        <Descriptions.Item label="结论" span={3}>{run.report.conclusion}</Descriptions.Item>
                        <Descriptions.Item label="状态"><Tag color={STATUS[run.status].color}>{STATUS[run.status].text}</Tag></Descriptions.Item>
                        <Descriptions.Item label="证据数">{run.report.evidence_count}</Descriptions.Item>
                        <Descriptions.Item label="审批人">{run.approval?.approver ?? '--'}</Descriptions.Item>
                      </Descriptions>
                      <Divider />
                      <Alert type="info" showIcon message={run.report.data_notice} />
                    </Card>
                  )}
                </Space>
              )}
        </Spin>
        </>}
        {activeView === 'tasks' && <TaskCenter history={history} onOpen={openRun} onRefresh={refreshHistory} />}
        {activeView === 'evaluation' && <EvaluationCenter />}
        {activeView === 'audit' && <AuditLogCenter />}
      </Content>

      <Modal
        title={<span><SafetyCertificateOutlined /> 高风险操作人工审批</span>}
        visible={approvalOpen}
        onCancel={() => setApprovalOpen(false)}
        width={720}
        footer={[
          <Button key="reject" onClick={() => decide(false)}>拒绝并安全终止</Button>,
          <Button key="approve" type="primary" danger onClick={() => decide(true)}>批准模拟执行</Button>,
        ]}
      >
        <Alert type="warning" showIcon message="以下操作会写入模拟 MES，系统将记录审批人与所有工具参数。" />
        <List
          className="action-list"
          dataSource={run?.state.action_plan ?? []}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                avatar={<ApiOutlined className="action-icon" />}
                title={<Space>{item.title}<Tag color={item.risk === 'high' ? 'red' : 'orange'}>{item.risk}</Tag></Space>}
                description={<><div>{item.description}</div><Text code>{item.tool}</Text></>}
              />
            </List.Item>
          )}
        />
        <label className="field-label">审批人</label>
        <Input value={user?.username ?? approver} disabled={AUTH_ENABLED} onChange={(event) => setApprover(event.target.value)} />
        <label className="field-label">审批意见</label>
        <TextArea value={comment} onChange={(event) => setComment(event.target.value)} rows={3} />
      </Modal>
      <Modal
        title={<span><SafetyCertificateOutlined /> FactoryPilot 身份认证</span>}
        visible={loginOpen}
        closable={!AUTH_ENABLED}
        maskClosable={false}
        footer={<Button type="primary" loading={loggingIn} onClick={login}>登录</Button>}
      >
        <Alert
          type="info"
          showIcon
          message="演示主管账号：supervisor / supervisor123"
          description="只有 SUPERVISOR 或 ADMIN 可以批准高风险操作。"
        />
        <label className="field-label">用户名</label>
        <Input prefix={<UserOutlined />} value={loginUsername} onChange={(event) => setLoginUsername(event.target.value)} />
        <label className="field-label">密码</label>
        <Input.Password value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} onPressEnter={login} />
      </Modal>
      </Layout>
    </Layout>
  )
}

function TaskCenter({
  history,
  onOpen,
  onRefresh,
}: {
  history: RunSummary[]
  onOpen: (id: string) => Promise<void>
  onRefresh: () => Promise<void>
}) {
  const completed = history.filter((item) => item.status === 'completed').length
  const pending = history.filter((item) => item.status === 'awaiting_approval').length
  const failed = history.filter((item) => item.status === 'failed').length

  return (
    <section className="page-view">
      <div className="page-title-row">
        <div>
          <Title level={2}>异常任务</Title>
          <Paragraph>查看诊断任务状态、风险等级和闭环结果，点击任务可进入控制台查看证据链。</Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => onRefresh()}>刷新任务</Button>
      </div>

      <div className="overview-strip">
        <div><span>任务总数</span><strong>{history.length}</strong><small>最近 30 条</small></div>
        <div className="success"><span>已完成</span><strong>{completed}</strong><small>闭环报告已生成</small></div>
        <div className="warning"><span>等待审批</span><strong>{pending}</strong><small>写操作仍被暂停</small></div>
        <div className="danger"><span>执行失败</span><strong>{failed}</strong><small>需要人工复核</small></div>
      </div>

      <Card className="surface data-card" bordered={false} title="全部异常任务" extra={<Text type="secondary">点击“查看详情”进入诊断控制台</Text>}>
        <Table<RunSummary>
          className="operations-table"
          rowKey="id"
          dataSource={history}
          scroll={{ x: 960 }}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          columns={[
            {
              title: '任务',
              dataIndex: 'query',
              width: 380,
              render: (value: string, item) => (
                <div className="table-primary">
                  <strong>{value}</strong>
                  <span>RUN {item.id.slice(0, 8).toUpperCase()}</span>
                </div>
              ),
            },
            { title: '产线', dataIndex: 'line_id', width: 110 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 120,
              render: (value: RunStatus) => <Tag color={STATUS[value].color}>{STATUS[value].text}</Tag>,
            },
            {
              title: '严重度',
              dataIndex: 'severity',
              width: 110,
              render: (value?: string) => value ? <Tag color={value === 'critical' ? 'red' : 'orange'}>{value}</Tag> : '--',
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              width: 180,
              render: (value: string) => new Date(value).toLocaleString('zh-CN'),
            },
            {
              title: '操作',
              key: 'action',
              fixed: 'right',
              width: 110,
              render: (_, item) => <Button type="link" onClick={() => onOpen(item.id)}>查看详情</Button>,
            },
          ]}
        />
      </Card>
    </section>
  )
}

function EvaluationCenter() {
  const [report, setReport] = useState<EvaluationReport | null>(null)
  const [loadingReport, setLoadingReport] = useState(true)
  const [error, setError] = useState('')

  const loadReport = useCallback(async () => {
    setLoadingReport(true)
    setError('')
    try {
      setReport(await api.evaluationReport())
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '评测报告加载失败')
    } finally {
      setLoadingReport(false)
    }
  }, [])

  useEffect(() => {
    loadReport()
  }, [loadReport])

  const percent = (value: number) => `${(value * 100).toFixed(1)}%`

  return (
    <section className="page-view">
      <div className="page-title-row">
        <div>
          <Title level={2}>评测中心</Title>
          <Paragraph>查看离线回归、安全场景和 LangSmith 实验结果，包括保留的失败案例。</Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loadingReport} onClick={loadReport}>重新加载</Button>
      </div>

      {error && <Alert type="error" showIcon message="无法读取评测报告" description={error} />}
      <Spin spinning={loadingReport}>
        {report && <>
          <div className="overview-strip evaluation-overview">
            <div><span>回归数据集</span><strong>{report.dataset_size}</strong><small>{report.profile}</small></div>
            <div className="success"><span>困难场景通过率</span><strong>{percent(report.metrics.scenario_pass_rate)}</strong><small>{report.counts.scenario_cases} 个场景</small></div>
            <div className="warning"><span>完整任务成功率</span><strong>{percent(report.metrics.complete_task_success_rate)}</strong><small>包含安全降级结果</small></div>
            <div className="success"><span>高风险违规率</span><strong>{percent(report.metrics.high_risk_violation_rate)}</strong><small>{report.counts.security_cases} 个安全案例</small></div>
          </div>

          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card className="surface data-card" bordered={false} title="版本回归对比" extra={<Text type="secondary">最近生成：{new Date(report.generated_at).toLocaleString('zh-CN')}</Text>}>
                <Table
                  className="operations-table"
                  rowKey="version"
                  dataSource={report.profile_comparison}
                  pagination={false}
                  scroll={{ x: 900 }}
                  columns={[
                    { title: '版本', dataIndex: 'version', width: 210 },
                    { title: '样本', dataIndex: 'sample_size', width: 80 },
                    { title: '任务成功率', dataIndex: 'complete_task_success_rate', width: 120, render: percent },
                    { title: '有据可依率', dataIndex: 'grounded_hypothesis_rate', width: 120, render: percent },
                    { title: '工具准确率', dataIndex: 'tool_selection_accuracy', width: 120, render: percent },
                    { title: '模型直出', dataIndex: 'model_success_rate', width: 110, render: (value?: number | null) => value == null ? '不适用' : percent(value) },
                    { title: 'P95', dataIndex: 'p95_response_time_ms', width: 110, render: (value: number) => `${value} ms` },
                    { title: '平均 Token', dataIndex: 'average_tokens', width: 110 },
                  ]}
                />
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Card className="surface quality-card" bordered={false} title="关键质量门禁">
                <div className="quality-gates">
                  <div><span>意图分类准确率</span><Progress percent={report.metrics.intent_classification_accuracy * 100} strokeColor="#4ee1b6" /></div>
                  <div><span>Recall@4</span><Progress percent={report.metrics.retrieval_recall_at_4 * 100} strokeColor="#67a7ff" /></div>
                  <div><span>根因引用有效率</span><Progress percent={report.metrics.grounded_hypothesis_rate * 100} strokeColor="#4ee1b6" /></div>
                  <div><span>工具选择准确率</span><Progress percent={report.metrics.tool_selection_accuracy * 100} strokeColor="#67a7ff" /></div>
                </div>
                {report.langsmith_experiment && (
                  <a className="langsmith-link" href={report.langsmith_experiment.experiment_url} target="_blank" rel="noreferrer">
                    <div><strong>{report.langsmith_experiment.experiment}</strong><span>{report.langsmith_experiment.run_count} 条托管运行</span></div>
                    <LinkOutlined />
                  </a>
                )}
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <Card className="surface" bordered={false} title={<span><WarningFilled /> 保留的失败案例</span>}>
                <List
                  dataSource={report.failed_cases}
                  renderItem={(item) => (
                    <List.Item className="failure-item">
                      <List.Item.Meta title={<Space><Tag color="red">{item.id}</Tag>{item.scenario}</Space>} description={`期望：${item.expected} · 实际：${item.actual}`} />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card className="surface" bordered={false} title="评测边界与限制">
                <List dataSource={report.limitations} renderItem={(item) => <List.Item className="limitation-item">{item}</List.Item>} />
              </Card>
            </Col>
          </Row>
        </>}
      </Spin>
    </section>
  )
}

function AuditLogCenter() {
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [loadingLogs, setLoadingLogs] = useState(true)
  const [error, setError] = useState('')

  const loadLogs = useCallback(async () => {
    setLoadingLogs(true)
    setError('')
    try {
      setLogs(await api.listAuditLogs(200))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '审计日志加载失败')
    } finally {
      setLoadingLogs(false)
    }
  }, [])

  useEffect(() => {
    loadLogs()
  }, [loadLogs])

  const tools = new Set(logs.map((item) => item.tool)).size
  const failed = logs.filter((item) => /fail|error|denied|blocked/i.test(item.outcome)).length
  const averageDuration = logs.length ? Math.round(logs.reduce((sum, item) => sum + item.duration_ms, 0) / logs.length) : 0

  return (
    <section className="page-view">
      <div className="page-title-row">
        <div>
          <Title level={2}>审计日志</Title>
          <Paragraph>查看 MES、知识检索与写操作工具调用，支持按任务追溯参数、结果和耗时。</Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loadingLogs} onClick={loadLogs}>刷新日志</Button>
      </div>

      {error && <Alert type="error" showIcon message="无法读取审计日志" description={error} />}
      <div className="overview-strip">
        <div><span>审计事件</span><strong>{logs.length}</strong><small>最近 200 条</small></div>
        <div className="success"><span>受控工具</span><strong>{tools}</strong><small>MCP / RAG / MES</small></div>
        <div className={failed ? 'danger' : 'success'}><span>失败或阻断</span><strong>{failed}</strong><small>安全失败同样留痕</small></div>
        <div><span>平均耗时</span><strong>{averageDuration} ms</strong><small>端到端工具调用</small></div>
      </div>

      <Card className="surface data-card" bordered={false} title="工具调用审计流水" extra={<Tag color="blue">数据库记录</Tag>}>
        <Table<AuditLog>
          className="operations-table audit-table"
          rowKey="id"
          loading={loadingLogs}
          dataSource={logs}
          pagination={{ pageSize: 12, showSizeChanger: false }}
          scroll={{ x: 1180 }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 180,
              render: (value: string) => new Date(value).toLocaleString('zh-CN'),
            },
            {
              title: '任务',
              dataIndex: 'run_id',
              width: 135,
              render: (value: string) => <Text code>{value.slice(0, 12)}</Text>,
            },
            { title: '执行者', dataIndex: 'actor', width: 120 },
            { title: '工具', dataIndex: 'tool', width: 140, render: (value: string) => <Tag color="blue">{value}</Tag> },
            { title: '操作', dataIndex: 'operation', width: 190 },
            {
              title: '受控参数',
              dataIndex: 'parameters',
              width: 300,
              render: (value: Record<string, unknown>) => <Text className="parameter-json">{JSON.stringify(value)}</Text>,
            },
            {
              title: '结果',
              dataIndex: 'outcome',
              width: 130,
              render: (value: string) => <Tag color={/fail|error|denied|blocked/i.test(value) ? 'red' : 'green'}>{value}</Tag>,
            },
            { title: '耗时', dataIndex: 'duration_ms', width: 90, render: (value: number) => `${value} ms` },
          ]}
        />
      </Card>
    </section>
  )
}

function EvidencePanel({ evidence }: { evidence: Evidence[] }) {
  const sourceLabel: Record<Evidence['source_type'], { text: string; color: string }> = {
    mes: { text: 'MES', color: 'blue' },
    alarm: { text: '告警', color: 'volcano' },
    quality: { text: '质量', color: 'cyan' },
    maintenance: { text: '维修', color: 'purple' },
    knowledge: { text: 'SOP / 手册', color: 'green' },
    analytics: { text: '统计分析', color: 'gold' },
    graph: { text: '故障图谱', color: 'geekblue' },
    a2a: { text: '厂商 A2A', color: 'magenta' },
  }
  return (
    <Card className="surface" bordered={false} title={<span><FileSearchOutlined /> 证据与段落引用</span>} extra={<Text type="secondary">所有结论可回溯</Text>}>
      <List
        grid={{ gutter: 16, xs: 1, md: 2 }}
        dataSource={evidence}
        renderItem={(item) => (
          <List.Item>
            <div className="evidence-card">
              <div className="evidence-head">
                <Space><Tag color={sourceLabel[item.source_type].color}>{sourceLabel[item.source_type].text}</Tag><Text strong>{item.title}</Text></Space>
                <Text className="score">{Math.round(item.score * 100)}%</Text>
              </div>
              <Paragraph ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}>{item.excerpt}</Paragraph>
              <Text className="source-ref">↳ {item.source_ref}</Text>
            </div>
          </List.Item>
        )}
      />
    </Card>
  )
}

export default App
