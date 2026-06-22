import {
  FundOutlined,
  LineChartOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ShareAltOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Slider,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'

import { api } from '../api.js'

const { RangePicker } = DatePicker

/* ── 自定义因子相关组件 ── */
function FactorDetailDrawer({ factor, open, onClose, onUpdate }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open || !factor) return
    form.setFieldsValue(factor)
  }, [open, factor, form])

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setBusy(true)
    try {
      await api.put(`/api/factors/${factor.id}`, values)
      message.success('因子已更新')
      onUpdate?.()
      onClose()
    } catch { /* api.js handles error */ }
    finally { setBusy(false) }
  }

  return (
    <Drawer
      title={factor ? `${factor.name_cn} (${factor.name})` : '因子详情'}
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
      extra={
        !factor?.is_builtin && (
          <Button type="primary" onClick={handleSave} loading={busy}>保存</Button>
        )
      }
    >
      {factor && (
        <Form form={form} layout="vertical">
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="分类">{factor.category}</Descriptions.Item>
            <Descriptions.Item label="子类">{factor.subcategory || '—'}</Descriptions.Item>
            <Descriptions.Item label="方向">
              <Tag color={factor.direction > 0 ? 'green' : 'red'}>
                {factor.direction > 0 ? '正向' : '反向'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="内置">
              <Tag color={factor.is_builtin ? 'blue' : 'purple'}>
                {factor.is_builtin ? '是' : '否'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
          <Divider />
          {!factor.is_builtin && (
            <>
              <Form.Item name="name_cn" label="中文名" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="category" label="分类">
                <Select options={[
                  { value: 'price_volume', label: '量价' },
                  { value: 'fundamental', label: '基本面' },
                  { value: 'industry', label: '行业' },
                  { value: 'custom', label: '自定义' },
                ]} />
              </Form.Item>
              <Form.Item name="subcategory" label="子类">
                <Input placeholder="动量/波动率/估值/..." />
              </Form.Item>
              <Form.Item name="direction" label="方向">
                <Radio.Group>
                  <Radio value={1}>正向(higher better)</Radio>
                  <Radio value={-1}>反向(lower better)</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item name="description" label="说明">
                <Input.TextArea rows={2} />
              </Form.Item>
              <Form.Item name="required_tables" label="依赖表">
                <Input placeholder="ods_tushare_daily,ods_tushare_daily_basic" />
              </Form.Item>
            </>
          )}
          <Form.Item label="SQL 公式" style={{ marginTop: 8 }}>
            <Typography.Text code style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {factor.formula_sql}
            </Typography.Text>
          </Form.Item>
          {factor.description && (
            <Form.Item label="说明">
              <Typography.Text type="secondary">{factor.description}</Typography.Text>
            </Form.Item>
          )}
        </Form>
      )}
    </Drawer>
  )
}

function NewFactorModal({ open, categories, onClose, onSuccess }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)

  const handleOk = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    setBusy(true)
    try {
      await api.post('/api/factors', values)
      message.success('因子已创建')
      onSuccess?.()
      onClose()
    } catch { /* api.js */ }
    finally { setBusy(false) }
  }

  return (
    <Modal
      title="新增自定义因子"
      open={open}
      onCancel={onClose}
      onOk={handleOk}
      confirmLoading={busy}
      destroyOnClose
      width={560}
    >
      <Form form={form} layout="vertical" initialValues={{ category: 'custom', direction: 1, cross_sectional: true, required_tables: 'ods_tushare_daily' }}>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="name" label="英文标识" rules={[{ required: true, message: '唯一标识' }, { pattern: /^[a-z0-9_]{3,32}$/, message: '小写/数字/下划线 3-32 位' }]}>
              <Input placeholder="my_factor" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="name_cn" label="中文名" rules={[{ required: true }]}>
              <Input placeholder="我的因子" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="category" label="分类">
          <Select options={categories.map(c => ({ value: c, label: c }))} placeholder="自定义" />
        </Form.Item>
        <Form.Item name="direction" label="方向">
          <Radio.Group>
            <Radio value={1}>正向</Radio>
            <Radio value={-1}>反向</Radio>
          </Radio.Group>
        </Form.Item>
        <Form.Item name="formula_sql" label="DuckDB SQL 公式" rules={[{ required: true, message: '请输入 SQL 表达式' }]}
          extra="示例: (close / LAG(close,20) OVER (PARTITION BY ts_code ORDER BY trade_date) - 1)">
          <Input.TextArea rows={4} placeholder="DuckDB SQL 表达式,列名引用 ods_tushare_daily 等表的字段" />
        </Form.Item>
        <Form.Item name="required_tables" label="依赖表">
          <Input placeholder="ods_tushare_daily" />
        </Form.Item>
        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

/* ── 主页面 ── */
export default function FactorResearch() {
  /* ── 状态 ── */
  const [factors, setFactors] = useState([])
  const [categories, setCategories] = useState([])
  const [loading, setLoading] = useState(false)

  /* Tab keys */
  const [activeTab, setActiveTab] = useState('library')

  /* Tab 1: 因子库 */
  const [filterCategory, setFilterCategory] = useState('__all__')
  const [drawerFactor, setDrawerFactor] = useState(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [newFModal, setNewFModal] = useState(false)

  /* Tab 2: 因子分析 */
  const [selectedFactorId, setSelectedFactorId] = useState(null)
  const [analysisDateRange, setAnalysisDateRange] = useState(['2024-01-01', '2026-06-22'])
  const [analysisResult, setAnalysisResult] = useState(null)
  const [analysisBusy, setAnalysisBusy] = useState(false)

  /* Tab 3: 因子组合 */
  const [combineFactors, setCombineFactors] = useState({})  // {name: weight}
  const [corrMatrix, setCorrMatrix] = useState(null)

  /* Tab 4: 策略回测 */
  const [strategyBusy, setStrategyBusy] = useState(false)
  const [btResult, setBtResult] = useState(null)
  const [btForm] = Form.useForm()

  /* ── 加载因子库 ── */
  const loadFactors = () => {
    setLoading(true)
    api.get('/api/factors')
      .then(setFactors)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  const loadCategories = () => {
    api.get('/api/factors/categories')
      .then(setCategories)
      .catch(() => {})
  }

  useEffect(() => { loadFactors(); loadCategories() }, [])

  const categoryLabels = useMemo(() => {
    const seen = new Set()
    const labels = []
    for (const f of factors) {
      if (!seen.has(f.category)) {
        seen.add(f.category)
        labels.push(f.category)
      }
    }
    return labels
  }, [factors])

  /* ── Tab 1 表格 ── */
  const filteredFactors = useMemo(() => {
    if (filterCategory === '__all__') return factors
    return factors.filter(f => f.category === filterCategory)
  }, [factors, filterCategory])

  const factorColumns = [
    { title: '英文名', dataIndex: 'name', width: 140, render: (v) => <Typography.Text code>{v}</Typography.Text> },
    { title: '中文名', dataIndex: 'name_cn', width: 120 },
    {
      title: '分类', dataIndex: 'category', width: 100,
      render: (v) => {
        const colors = { price_volume: 'blue', fundamental: 'green', industry: 'orange', custom: 'purple' }
        return <Tag color={colors[v] ?? 'default'}>{v}</Tag>
      },
    },
    { title: '子类', dataIndex: 'subcategory', width: 80 },
    {
      title: '方向', dataIndex: 'direction', width: 60,
      render: (v) => <Tag color={v > 0 ? 'green' : 'red'}>{v > 0 ? '正向' : '反向'}</Tag>,
    },
    {
      title: '说明', dataIndex: 'description', ellipsis: true,
      render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Typography.Text>,
    },
    {
      title: '操作', width: 60,
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => { setDrawerFactor(row); setDrawerOpen(true) }}>详情</Button>
      ),
    },
  ]

  /* ── Tab 2: 分析 ── */
  const handleAnalyze = async () => {
    if (!selectedFactorId) { message.warning('请选择一个因子'); return }
    setAnalysisBusy(true)
    setAnalysisResult(null)
    try {
      const [start, end] = analysisDateRange
      const result = await api.get(
        `/api/factors/${selectedFactorId}/analysis?start_date=${start}&end_date=${end}`
      )
      setAnalysisResult(result)
    } catch { /* api handles */ }
    finally { setAnalysisBusy(false) }
  }

  /* ── Tab 3: 组合 ── */
  const handleCorrMatrix = async () => {
    const names = Object.keys(combineFactors)
    if (names.length < 2) { message.warning('至少选择 2 个因子'); return }
    try {
      const r = await api.post('/api/factors/correlation-matrix', names)
      setCorrMatrix(r)
    } catch { /* api */ }
  }

  const handleCombine = async () => {
    const names = Object.keys(combineFactors)
    if (!names.length) { message.warning('请添加因子并设权重'); return }
    try {
      const r = await api.post('/api/factors/combine', combineFactors)
      message.success(`合成完成: top_avg=${r.composite_ic?.top_avg?.toFixed(6)}`)
    } catch { /* api */ }
  }

  /* ── Tab 4: 回测 ── */
  const handleBacktest = async () => {
    let values
    try { values = await btForm.validateFields() } catch { return }
    setStrategyBusy(true)
    setBtResult(null)
    try {
      const r = await api.post('/api/strategies', {
        name: `backtest_${Date.now()}`,
        factor_weights: combineFactors,
        top_n: values.top_n ?? 30,
        rebalance_freq: values.rebalance_freq ?? 'monthly',
        weight_scheme: values.weight_scheme ?? 'equal',
        transaction_cost_bps: values.transaction_cost_bps ?? 30,
        start_date: (values.date_range ?? ['2024-01-01', '2026-06-01'])[0],
        end_date: (values.date_range ?? ['2024-01-01', '2026-06-01'])[1],
      })
      const sid = r.id
      const br = await api.post(`/api/strategies/${sid}/backtest`)
      setBtResult(br)
    } catch { /* api */ }
    finally { setStrategyBusy(false) }
  }

  /* ── Render ── */
  const tabItems = [
    {
      key: 'library',
      label: <span><FundOutlined /> 因子库</span>,
      children: (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
            <Space>
              <Select
                style={{ width: 140 }}
                value={filterCategory}
                onChange={setFilterCategory}
                options={[
                  { value: '__all__', label: '全部分类' },
                  ...categoryLabels.map(c => ({ value: c, label: c })),
                ]}
              />
              <Typography.Text type="secondary">{filteredFactors.length} 个因子</Typography.Text>
            </Space>
            <Button icon={<PlusOutlined />} type="primary" onClick={() => setNewFModal(true)}>
              新增因子
            </Button>
          </div>
          <Table
            rowKey="id"
            size="small"
            loading={loading}
            dataSource={filteredFactors}
            columns={factorColumns}
            pagination={false}
          />
        </div>
      ),
    },
    {
      key: 'analysis',
      label: <span><LineChartOutlined /> 因子分析</span>,
      children: (
        <div>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card size="small" title="选择因子">
              <Space wrap>
                <Select
                  style={{ width: 280 }}
                  showSearch
                  placeholder="选择因子"
                  value={selectedFactorId}
                  onChange={setSelectedFactorId}
                  options={factors.map(f => ({ value: f.id, label: `${f.name_cn} (${f.name})` }))}
                  filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())}
                />
                <RangePicker
                  value={analysisDateRange?.length === 2
                    ? [dayjs(analysisDateRange[0]), dayjs(analysisDateRange[1])] : null}
                  onChange={(vals) => vals
                    ? setAnalysisDateRange([vals[0].format('YYYY-MM-DD'), vals[1].format('YYYY-MM-DD')])
                    : setAnalysisDateRange(null)}
                />
                <Button type="primary" loading={analysisBusy} onClick={handleAnalyze}>运行分析</Button>
              </Space>
            </Card>

            {analysisResult?.ic_summary && (
              <Card size="small" title="IC 摘要">
                <Row gutter={16}>
                  <Col span={6}><Statistic title="Mean IC" value={analysisResult.ic_summary.mean_ic} precision={4} /></Col>
                  <Col span={6}><Statistic title="IC IR" value={analysisResult.ic_summary.ic_ir} precision={3} /></Col>
                  <Col span={6}><Statistic title="Win Rate" value={analysisResult.ic_summary.win_rate} precision={2} suffix="%" valueStyle={{ color: analysisResult.ic_summary.win_rate > 0.5 ? '#3f8600' : '#cf1322' }} /></Col>
                  <Col span={6}><Statistic title="N Days" value={analysisResult.ic_summary.n_days} /></Col>
                </Row>
              </Card>
            )}

            {analysisResult?.ic_series?.length > 0 && (
              <Card size="small" title="IC 时间序列(最近 60 日)">
                <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                  {analysisResult.ic_series.slice(-60).map(p => `${p.date}: ${p.pearson_ic?.toFixed(4)}`).join(' | ')}
                </Typography.Paragraph>
              </Card>
            )}

            {analysisResult?.decay?.length > 0 && (
              <Card size="small" title="IC 衰减">
                <Space>
                  {analysisResult.decay.map(d => (
                    <Tag key={d.horizon_days} color="blue">{d.horizon_days}d: {d.mean_ic?.toFixed(4)}</Tag>
                  ))}
                </Space>
              </Card>
            )}
          </Space>
        </div>
      ),
    },
    {
      key: 'combine',
      label: <span><ShareAltOutlined /> 因子组合</span>,
      children: (
        <div>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card size="small" title="选择因子并设权重">
              <Select
                mode="multiple"
                style={{ minWidth: 400 }}
                placeholder="搜索并添加因子"
                value={Object.keys(combineFactors)}
                onChange={(names) => {
                  const next = { ...combineFactors }
                  for (const n of names) {
                    if (!(n in next)) next[n] = 1 / names.length
                  }
                  for (const k of Object.keys(next)) {
                    if (!names.includes(k)) delete next[k]
                  }
                  setCombineFactors(next)
                }}
                options={factors.map(f => ({ value: f.name, label: `${f.name_cn} (${f.name})` }))}
                filterOption={(input, option) => option.label.toLowerCase().includes(input.toLowerCase())}
              />
              {Object.keys(combineFactors).map(name => (
                <div key={name} style={{ marginTop: 8 }}>
                  <Typography.Text>{name}</Typography.Text>
                  <Slider
                    style={{ width: 300, marginLeft: 16 }}
                    min={0} max={1} step={0.05}
                    value={combineFactors[name]}
                    onChange={(v) => setCombineFactors(prev => ({ ...prev, [name]: v }))}
                  />
                </div>
              ))}
              <div style={{ marginTop: 12 }}>
                <Space>
                  <Button onClick={handleCombine} type="primary">合成计算</Button>
                  <Button onClick={handleCorrMatrix}>相关性矩阵</Button>
                </Space>
              </div>
            </Card>

            {corrMatrix && (
              <Card size="small" title="相关性矩阵">
                <Table
                  size="small"
                  pagination={false}
                  rowKey="0"
                  columns={[
                    { title: '', dataIndex: 'factor', width: 100 },
                    ...corrMatrix.factors.map((f, i) => ({
                      title: f, dataIndex: String(i), width: 100,
                      render: (v) => (
                        <Typography.Text style={{ color: Math.abs(v) > 0.7 ? '#cf1322' : Math.abs(v) > 0.4 ? '#d48806' : '#389e0d' }}>
                          {v?.toFixed(3)}
                        </Typography.Text>
                      ),
                    })),
                  ]}
                  dataSource={corrMatrix.matrix.map((row, i) => {
                    const entry = { factor: corrMatrix.factors[i], key: corrMatrix.factors[i] }
                    row.forEach((v, j) => { entry[String(j)] = v })
                    return entry
                  })}
                />
              </Card>
            )}
          </Space>
        </div>
      ),
    },
    {
      key: 'backtest',
      label: <span><PlayCircleOutlined /> 策略回测</span>,
      children: (
        <div>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card size="small" title="回测参数">
              <Form form={btForm} layout="vertical" initialValues={{ top_n: 30, rebalance_freq: 'monthly', weight_scheme: 'equal', transaction_cost_bps: 30 }}>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="top_n" label="选股数">
                      <InputNumber min={5} max={100} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="rebalance_freq" label="调仓频率">
                      <Select options={[
                        { value: 'monthly', label: '月度' },
                        { value: 'weekly', label: '周度' },
                        { value: 'daily', label: '每日' },
                      ]} />
                    </Form.Item>
                  </Col>
                  <Col span={8}>
                    <Form.Item name="weight_scheme" label="权重方案">
                      <Select options={[
                        { value: 'equal', label: '等权' },
                        { value: 'score', label: '分数加权' },
                      ]} />
                    </Form.Item>
                  </Col>
                </Row>
                <Row gutter={16}>
                  <Col span={8}>
                    <Form.Item name="transaction_cost_bps" label="交易成本(万分之)">
                      <InputNumber min={0} max={100} style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                  <Col span={16}>
                    <Form.Item name="date_range" label="回测区间">
                      <RangePicker style={{ width: '100%' }} />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" loading={strategyBusy} onClick={handleBacktest} icon={<PlayCircleOutlined />} block>
                  运行回测
                </Button>
              </Form>
            </Card>

            {btResult?.metrics && (
              <Card size="small" title="回测绩效">
                <Row gutter={[16, 16]}>
                  <Col span={6}><Statistic title="累计收益" value={btResult.metrics.cumulative_return} precision={4} prefix={btResult.metrics.cumulative_return >= 0 ? '+' : ''} valueStyle={{ color: btResult.metrics.cumulative_return >= 0 ? '#3f8600' : '#cf1322' }} /></Col>
                  <Col span={6}><Statistic title="年化收益" value={btResult.metrics.annual_return} precision={4} prefix={btResult.metrics.annual_return >= 0 ? '+' : ''} valueStyle={{ color: btResult.metrics.annual_return >= 0 ? '#3f8600' : '#cf1322' }} /></Col>
                  <Col span={6}><Statistic title="Sharpe" value={btResult.metrics.sharpe_ratio} precision={3} /></Col>
                  <Col span={6}><Statistic title="Max Drawdown" value={btResult.metrics.max_drawdown} precision={4} valueStyle={{ color: '#cf1322' }} /></Col>
                  <Col span={6}><Statistic title="日胜率" value={(btResult.metrics.daily_win_rate * 100).toFixed(1)} suffix="%" /></Col>
                  <Col span={6}><Statistic title="月胜率" value={(btResult.metrics.monthly_win_rate * 100).toFixed(1)} suffix="%" /></Col>
                  <Col span={6}><Statistic title="IR" value={btResult.metrics.information_ratio} precision={3} /></Col>
                  <Col span={6}><Statistic title="年数" value={btResult.metrics.n_years} precision={2} /></Col>
                </Row>
              </Card>
            )}

            {btResult?.daily_returns?.length > 0 && (
              <Card size="small" title="日收益摘要（最近 30 天）">
                <Typography.Paragraph style={{ fontSize: 12, maxHeight: 200, overflow: 'auto' }}>
                  {btResult.daily_returns.slice(-30).map(r =>
                    `${r.trade_date}: strat=${r.strategy_return?.toFixed(4)} bench=${r.bench_return?.toFixed(4)} excess=${r.excess_return?.toFixed(4)}`
                  ).join('\n')}
                </Typography.Paragraph>
              </Card>
            )}
          </Space>
        </div>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>因子研究</Typography.Title>
          <Typography.Text type="secondary">
            HS300 成分股量化因子库 — 定义、分析、组合、回测
          </Typography.Text>
        </div>
      </div>

      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />

      {/* Drawer: 因子详情 */}
      <FactorDetailDrawer
        factor={drawerFactor}
        open={drawerOpen}
        onClose={() => { setDrawerOpen(false); setDrawerFactor(null) }}
        onUpdate={loadFactors}
      />

      {/* Modal: 新增因子 */}
      <NewFactorModal
        open={newFModal}
        categories={categoryLabels}
        onClose={() => setNewFModal(false)}
        onSuccess={loadFactors}
      />
    </div>
  )
}
