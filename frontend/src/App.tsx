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
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'

import { api } from './api'
import type { AuthUser, Evidence, RunDetail, RunStatus, RunSummary, TimelineEvent } from './types'

const { Header, Content } = Layout
const { Text, Title, Paragraph } = Typography
const { TextArea } = Input

const DEFAULT_QUERY = '2号产线激光打码设备良率从 98% 降到 91%，帮我分析原因并给出处置方案。'
const AUTH_ENABLED = (import.meta.env.VITE_AUTH_ENABLED ?? 'true') === 'true'

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
  const [query, setQuery] = useState(DEFAULT_QUERY)
  const [run, setRun] = useState<RunDetail | null>(null)
  const [history, setHistory] = useState<RunSummary[]>([])
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(false)
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approver, setApprover] = useState('生产主管-张工')
  const [comment, setComment] = useState('批准模拟处置，执行后继续加严检验。')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginUsername, setLoginUsername] = useState('supervisor')
  const [loginPassword, setLoginPassword] = useState('supervisor123')
  const [loggingIn, setLoggingIn] = useState(false)
  const eventSource = useRef<EventSource | null>(null)

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.listRuns())
    } catch {
      // The main panel surfaces connectivity failures; history is non-critical.
    }
  }, [])

  const refreshRun = useCallback(async (id: string) => {
    const detail = await api.getRun(id)
    setRun(detail)
    return detail
  }, [])

  const connectEvents = useCallback(
    (id: string) => {
      eventSource.current?.close()
      const source = new EventSource(api.eventUrl(id), { withCredentials: true })
      eventSource.current = source
      source.addEventListener('timeline', () => {
        refreshRun(id).catch(() => undefined)
      })
      source.addEventListener('done', () => {
        refreshRun(id).catch(() => undefined)
        refreshHistory()
        source.close()
      })
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) return
      }
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
      const detail = await api.getRun(created.id)
      setRun(detail)
      connectEvents(created.id)
      refreshHistory()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '任务创建失败')
    } finally {
      setCreating(false)
    }
  }

  const openRun = async (id: string) => {
    setLoading(true)
    try {
      const detail = await refreshRun(id)
      if (!['completed', 'rejected', 'failed'].includes(detail.status)) connectEvents(id)
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
      await refreshRun(run.id)
      connectEvents(run.id)
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
          lineStyle: { color: '#37d3aa', width: 3 },
          itemStyle: { color: '#37d3aa', borderColor: '#07111f', borderWidth: 2 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(55,211,170,.28)' },
                { offset: 1, color: 'rgba(55,211,170,0)' },
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
      <Header className="app-header">
        <div className="brand">
          <div className="brand-mark"><ThunderboltFilled /></div>
          <div>
            <div className="brand-name">FactoryPilot</div>
            <div className="brand-caption">MES 异常诊断与闭环处置中心</div>
          </div>
        </div>
        <Space size="large">
          <Badge status="processing" text={<span className="muted">Agent Service 在线</span>} />
          {user && (
            <Space className="identity">
              <UserOutlined />
              <span>{user.display_name}</span>
              <Tag>{user.roles.includes('SUPERVISOR') ? '主管' : '操作员'}</Tag>
              <Button type="text" size="small" icon={<LogoutOutlined />} onClick={logout} />
            </Space>
          )}
          <Tag className="simulation-tag">SIMULATION</Tag>
        </Space>
      </Header>

      <Content className="app-content">
        <section className="command-panel">
          <div>
            <Text className="eyebrow">INCIDENT COMMAND</Text>
            <Title level={2}>今天要诊断哪一个生产异常？</Title>
            <Paragraph className="lead">输入自然语言问题，系统会查询生产、设备、质量与知识数据，并在任何写操作前暂停审批。</Paragraph>
          </div>
          <div className="query-box">
            <TextArea value={query} onChange={(event) => setQuery(event.target.value)} autoSize={{ minRows: 2, maxRows: 4 }} maxLength={500} />
            <Button type="primary" size="large" icon={<PlayCircleFilled />} loading={creating} onClick={startDiagnosis}>
              启动诊断
            </Button>
          </div>
        </section>

        <Row gutter={[20, 20]}>
          <Col xs={24} xl={6}>
            <Card className="surface history-card" bordered={false}>
              <div className="section-heading">
                <div><ClockCircleOutlined /> 最近任务</div>
                <Text type="secondary">{history.length} 条</Text>
              </div>
              <List
                dataSource={history}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" /> }}
                renderItem={(item) => (
                  <button className={`history-item ${run?.id === item.id ? 'active' : ''}`} onClick={() => openRun(item.id)}>
                    <div className="history-top">
                      <Text>{item.line_id}</Text>
                      <Tag color={STATUS[item.status].color}>{STATUS[item.status].text}</Tag>
                    </div>
                    <div className="history-query">{item.query}</div>
                    <div className="history-time">{new Date(item.created_at).toLocaleString()}</div>
                  </button>
                )}
              />
            </Card>
          </Col>

          <Col xs={24} xl={18}>
            <Spin spinning={loading}>
              {!run ? (
                <Card className="surface empty-stage" bordered={false}>
                  <RobotOutlined />
                  <Title level={3}>等待异常输入</Title>
                  <Text type="secondary">上方示例已经准备好，点击“启动诊断”即可观看完整工作流。</Text>
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

                  <Row gutter={[20, 20]}>
                    <Col xs={24} lg={10}>
                      <Card className="surface timeline-card" bordered={false} title={<span><RobotOutlined /> Agent 执行时间线</span>}>
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
                    </Col>
                    <Col xs={24} lg={14}>
                      <Space direction="vertical" size={20} style={{ width: '100%' }}>
                        <Card className="surface" bordered={false} title="最近20批次良率趋势与验证窗口">
                          {(run.state.quality?.length ?? 0) > 0 ? <ReactECharts option={chartOption} style={{ height: 280 }} /> : <Empty description="等待质量数据" />}
                          {run.state.verification && <Alert type="info" showIcon message={run.state.verification.note} />}
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
                              type={productionAnalysis.recovery_validation.status === 'passed' ? 'success' : 'info'}
                              showIcon
                              message={productionAnalysis.recovery_validation.status === 'pending'
                                ? '处置后连续三批恢复验证：等待执行'
                                : `连续达标 ${productionAnalysis.recovery_validation.current_consecutive_batches ?? 0}/${productionAnalysis.recovery_validation.required_consecutive_batches} 批`}
                            />
                          </Card>
                        )}

                        {topCause && (
                          <Card className="surface cause-card" bordered={false} title="首要根因假设">
                            <div className="cause-top">
                              <div>
                                <Text className="eyebrow">PRIMARY HYPOTHESIS</Text>
                                <Title level={4}>{topCause.cause}</Title>
                              </div>
                              <Progress type="circle" width={74} percent={Math.round(topCause.confidence * 100)} strokeColor="#31d6a7" trailColor="#1a2a3e" />
                            </div>
                            <Paragraph>{topCause.reasoning}</Paragraph>
                            <Space wrap>{topCause.evidence_ids.map((id) => <Tag key={id}>{id}</Tag>)}</Space>
                          </Card>
                        )}
                      </Space>
                    </Col>
                  </Row>

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
                    <Card className="surface report-card" bordered={false} title={<span><CheckCircleFilled /> 事故报告 {run.report.report_id}</span>}>
                      <Descriptions column={{ xs: 1, md: 3 }}>
                        <Descriptions.Item label="结论" span={3}>{run.report.conclusion}</Descriptions.Item>
                        <Descriptions.Item label="状态"><Tag color={STATUS[run.status].color}>{STATUS[run.status].text}</Tag></Descriptions.Item>
                        <Descriptions.Item label="证据数">{run.report.evidence_count}</Descriptions.Item>
                        <Descriptions.Item label="审批人">{run.approval?.approver ?? '--'}</Descriptions.Item>
                      </Descriptions>
                      <Divider />
                      <Alert type="success" showIcon message={run.report.data_notice} />
                    </Card>
                  )}
                </Space>
              )}
            </Spin>
          </Col>
        </Row>
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
