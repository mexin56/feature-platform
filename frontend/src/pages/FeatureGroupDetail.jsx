import {
  ArrowLeftOutlined,
  BugOutlined,
  EditOutlined,
  MinusCircleOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  message,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../api.js'
import Chart from '../components/Chart.jsx'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

// Build ECharts graph option from lineage edges + detail data
function buildLineageOption(edges, detail) {
  // category: 0=上游表, 1=特征组, 2=工作流
  const categories = [
    { name: '上游表', itemStyle: { color: '#5470c6' } },
    { name: '特征组', itemStyle: { color: '#91cc75' } },
    { name: '工作流', itemStyle: { color: '#fac858' } },
  ]

  const nodeMap = {}
  const addNode = (id, name, category) => {
    if (!nodeMap[id]) nodeMap[id] = { id, name, category, symbolSize: 40 }
  }

  // Seed this feature group
  const fgNodeId = `feature_group:${detail.id}`
  addNode(fgNodeId, `${detail.name}\nv${detail.version}`, 1)

  // Add upstream_tables from detail (these are edges: src→fgNodeId)
  for (const src of detail.upstream_tables ?? []) {
    addNode(src, src, 0)
  }

  // Add downstream (workflow) if bound
  if (detail.workflow_id) {
    const wfNodeId = `workflow:${detail.workflow_id}`
    addNode(wfNodeId, `工作流 #${detail.workflow_id}`, 2)
  }

  // Build link set from full lineage edges that touch this FG
  const links = []
  const linkSet = new Set()
  for (const e of edges) {
    // Only include edges that involve this feature group
    if (e.dst !== fgNodeId && e.src !== fgNodeId) continue
    const key = `${e.src}|${e.dst}`
    if (linkSet.has(key)) continue
    linkSet.add(key)

    // Ensure nodes exist for both sides
    if (e.src !== fgNodeId) {
      // upstream table → FG
      const cat = e.src.startsWith('feature_group:') ? 1
        : e.src.startsWith('workflow:') ? 2 : 0
      addNode(e.src, e.src.replace(/^(feature_group:|workflow:)/, ''), cat)
    }
    if (e.dst !== fgNodeId) {
      const cat = e.dst.startsWith('feature_group:') ? 1
        : e.dst.startsWith('workflow:') ? 2 : 0
      addNode(e.dst, e.dst.replace(/^(feature_group:|workflow:)/, ''), cat)
    }

    links.push({ source: e.src, target: e.dst, lineStyle: { color: '#aaa' } })
  }

  // If workflow bound, add edge FG→workflow (may not be in lineage edges)
  if (detail.workflow_id) {
    const wfNodeId = `workflow:${detail.workflow_id}`
    const key = `${fgNodeId}|${wfNodeId}`
    if (!linkSet.has(key)) {
      linkSet.add(key)
      links.push({ source: fgNodeId, target: wfNodeId, lineStyle: { color: '#aaa' } })
    }
  }

  // Also add upstream_tables edges that may not be in lineage store
  for (const src of detail.upstream_tables ?? []) {
    const key = `${src}|${fgNodeId}`
    if (!linkSet.has(key)) {
      linkSet.add(key)
      links.push({ source: src, target: fgNodeId, lineStyle: { color: '#aaa' } })
    }
  }

  const nodes = Object.values(nodeMap).map((n) => ({
    ...n,
    label: { show: true, formatter: n.name },
    itemStyle: categories[n.category]?.itemStyle,
  }))

  return {
    tooltip: { trigger: 'item', formatter: (p) => p.data.name ?? p.data.id ?? '' },
    legend: [{ data: categories.map((c) => c.name) }],
    series: [{
      type: 'graph',
      layout: 'force',
      categories,
      roam: true,
      draggable: true,
      nodes,
      links,
      emphasis: { focus: 'adjacency' },
      force: { repulsion: 200, edgeLength: 150 },
      edgeSymbol: ['none', 'arrow'],
      lineStyle: { width: 2, curveness: 0.1 },
      label: { show: true, position: 'bottom', fontSize: 12 },
    }],
  }
}

export default function FeatureGroupDetail() {
  const { id } = useParams()
  const navigate = useNavigate()

  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(true)
  const [lineageEdges, setLineageEdges] = useState([])
  const [workflows, setWorkflows] = useState([])
  const [taskKeys, setTaskKeys] = useState([])

  // Edit drawer
  const [editOpen, setEditOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  // Debug console
  const [debugKeys, setDebugKeys] = useState([{}])
  const [debugResult, setDebugResult] = useState(null)
  const [debugging, setDebugging] = useState(false)

  const load = async () => {
    setLoadingDetail(true)
    try {
      const d = await api.get(`/api/feature-groups/${id}`)
      setDetail(d)
      // Prime debug key row with entity_keys
      if (d.entity_keys?.length) {
        const row = {}
        d.entity_keys.forEach((k) => { row[k] = '' })
        setDebugKeys([row])
      }
    } catch {
      message.error('加载特征组详情失败')
    } finally {
      setLoadingDetail(false)
    }
  }

  const loadLineage = () => {
    api.get('/api/lineage').then(setLineageEdges).catch(() => {})
  }

  const loadWorkflows = () => {
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
  }

  useEffect(() => { load(); loadLineage(); loadWorkflows() }, [id])

  // ── Edit drawer helpers ──
  const onWorkflowChange = async (wid) => {
    form.setFieldValue('task_key', undefined)
    setTaskKeys([])
    if (!wid) return
    try {
      const wf = await api.get(`/api/workflows/${wid}`)
      setTaskKeys((wf.dag?.nodes ?? []).map((n) => n.key))
    } catch { /* ignore */ }
  }

  const openEdit = async () => {
    if (!detail) return
    if (detail.workflow_id) {
      try {
        const wf = await api.get(`/api/workflows/${detail.workflow_id}`)
        setTaskKeys((wf.dag?.nodes ?? []).map((n) => n.key))
      } catch { setTaskKeys([]) }
    } else {
      setTaskKeys([])
    }
    form.setFieldsValue({
      name: detail.name,
      description: detail.description,
      entity_keys: detail.entity_keys,
      event_time_col: detail.event_time_col ?? '',
      ttl_days: detail.ttl_days ?? undefined,
      online_enabled: detail.online_enabled,
      offline_kind: detail.offline_kind,
      offline_location: detail.offline_location,
      workflow_id: detail.workflow_id ?? undefined,
      task_key: detail.task_key ?? undefined,
      upstream_tables: detail.upstream_tables ?? [],
      features: detail.features ?? [],
    })
    setEditOpen(true)
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }

    const entity_keys = Array.isArray(values.entity_keys)
      ? values.entity_keys
      : values.entity_keys ? [values.entity_keys] : []

    const payload = {
      name: values.name,
      description: values.description ?? '',
      entity_keys,
      event_time_col: values.event_time_col || null,
      ttl_days: values.ttl_days ?? null,
      online_enabled: values.online_enabled ?? false,
      offline_kind: values.offline_kind,
      offline_location: values.offline_location,
      workflow_id: values.workflow_id ?? null,
      task_key: values.task_key ?? null,
      upstream_tables: values.upstream_tables ?? [],
      features: (values.features ?? []).map((f) => ({
        name: f.name,
        dtype: f.dtype || 'double',
        description: f.description ?? '',
      })),
    }

    setSaving(true)
    try {
      await api.put(`/api/feature-groups/${id}`, payload)
      message.success('特征组已更新')
      setEditOpen(false)
      load()
      loadLineage()
    } catch (e) {
      if (e.message && e.message.includes('已有更新版本')) {
        message.warning(e.message)
      }
    } finally {
      setSaving(false)
    }
  }

  // ── Debug console ──
  const entityKeys = detail?.entity_keys ?? []

  const setDebugKeyValue = (rowIdx, col, val) => {
    setDebugKeys((prev) => {
      const next = [...prev]
      next[rowIdx] = { ...next[rowIdx], [col]: val }
      return next
    })
  }

  const addDebugRow = () => {
    const row = {}
    entityKeys.forEach((k) => { row[k] = '' })
    setDebugKeys((prev) => [...prev, row])
  }

  const removeDebugRow = (idx) => {
    setDebugKeys((prev) => prev.filter((_, i) => i !== idx))
  }

  const runDebug = async () => {
    if (!detail?.online_enabled) {
      message.warning('该特征组未启用在线服务')
      return
    }
    setDebugging(true)
    setDebugResult(null)
    try {
      const result = await api.post(`/api/feature-groups/${id}/online-debug`, { keys: debugKeys })
      setDebugResult(result)
    } catch { /* error shown by api.js */ } finally {
      setDebugging(false)
    }
  }

  // ── Feature columns ──
  const featureColumns = [
    { title: '特征名', dataIndex: 'name', width: 200 },
    { title: '数据类型', dataIndex: 'dtype', width: 120 },
    { title: '口径说明', dataIndex: 'description', render: (v) => v || '—' },
  ]

  // ── Debug result rendering ──
  const renderDebugResults = () => {
    if (!debugResult) return null
    const { results } = debugResult
    return (
      <div style={{ marginTop: 12 }}>
        {(results ?? []).map((r, i) => (
          <Card
            key={i}
            size="small"
            style={{ marginBottom: 8, border: r.expired ? '1px solid #ff4d4f' : undefined }}
            title={
              <Space>
                <span>主键: {JSON.stringify(r.key)}</span>
                {r.expired && <Tag color="red">已过期 (expired)</Tag>}
              </Space>
            }
          >
            {r.values != null ? (
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {JSON.stringify(r.values, null, 2)}
              </pre>
            ) : (
              <Typography.Text type="secondary">{r.expired ? 'TTL 已过期，值已屏蔽' : '未命中（无记录）'}</Typography.Text>
            )}
            {r.event_time && (
              <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                event_time: {r.event_time} | updated_at: {r.updated_at}
              </div>
            )}
          </Card>
        ))}
      </div>
    )
  }

  if (loadingDetail) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!detail) {
    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/feature-groups')} style={{ marginBottom: 16 }}>
          返回列表
        </Button>
        <Typography.Text type="danger">特征组不存在或无访问权限</Typography.Text>
      </div>
    )
  }

  const lineageOption = buildLineageOption(lineageEdges, detail)

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/feature-groups')}>返回</Button>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {detail.name}
              <Tag color="blue" style={{ marginLeft: 8, fontWeight: 400, fontSize: 13 }}>v{detail.version}</Tag>
            </Typography.Title>
            <Typography.Text type="secondary">{detail.description || '暂无描述'}</Typography.Text>
          </div>
        </Space>
        <Button icon={<EditOutlined />} onClick={openEdit}>编辑</Button>
      </div>

      {/* Metadata */}
      <Card style={{ marginBottom: 20 }}>
        <Descriptions column={3} size="small" bordered>
          <Descriptions.Item label="在线服务">
            <Tag color={detail.online_enabled ? 'green' : 'default'}>
              {detail.online_enabled ? '开启' : '关闭'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="主键列">
            {(detail.entity_keys ?? []).map((k) => <Tag key={k}>{k}</Tag>)}
          </Descriptions.Item>
          <Descriptions.Item label="事件时间列">{detail.event_time_col || '—'}</Descriptions.Item>
          <Descriptions.Item label="TTL（天）">{detail.ttl_days ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="离线落地">{detail.offline_kind}</Descriptions.Item>
          <Descriptions.Item label="存储路径">{detail.offline_location}</Descriptions.Item>
          <Descriptions.Item label="绑定工作流">{detail.workflow_id ? `#${detail.workflow_id}` : '—'}</Descriptions.Item>
          <Descriptions.Item label="绑定节点">{detail.task_key || '—'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{fmt(detail.created_at)}</Descriptions.Item>
          <Descriptions.Item label="最近产出时间">{fmt(detail.last_produced_at)}</Descriptions.Item>
          <Descriptions.Item label="最近产出行数">{detail.last_produced_rows ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="物化水位">{fmt(detail.materialize_watermark)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Row gutter={20}>
        {/* Features Table */}
        <Col span={24}>
          <Card
            title="特征清单"
            style={{ marginBottom: 20 }}
            extra={<Typography.Text type="secondary">{detail.features?.length ?? 0} 个特征</Typography.Text>}
          >
            <Table
              rowKey="name"
              size="small"
              dataSource={detail.features ?? []}
              columns={featureColumns}
              pagination={false}
              locale={{ emptyText: '暂无特征' }}
            />
          </Card>
        </Col>
      </Row>

      {/* Lineage Graph */}
      <Card title="血缘图" style={{ marginBottom: 20 }}>
        <div style={{ marginBottom: 8 }}>
          <Tag color="#5470c6">上游表</Tag>
          <Tag color="#91cc75">特征组</Tag>
          <Tag color="#fac858">工作流</Tag>
        </div>
        {(lineageEdges.length === 0 && (detail.upstream_tables ?? []).length === 0 && !detail.workflow_id) ? (
          <Typography.Text type="secondary">暂无血缘关系</Typography.Text>
        ) : (
          <Chart option={lineageOption} height={360} />
        )}
      </Card>

      {/* Online Debug Console */}
      <Card
        title={
          <Space>
            <BugOutlined />
            在线调试台
            {!detail.online_enabled && <Tag color="orange">未启用在线服务</Tag>}
          </Space>
        }
      >
        {entityKeys.length === 0 ? (
          <Typography.Text type="secondary">无主键列配置</Typography.Text>
        ) : (
          <>
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              按主键列（{entityKeys.join(', ')}）输入查询值
            </Typography.Text>

            {debugKeys.map((row, rowIdx) => (
              <Space key={rowIdx} style={{ display: 'flex', marginBottom: 8, flexWrap: 'wrap' }} align="center">
                <span style={{ color: '#888', width: 28, textAlign: 'right' }}>#{rowIdx + 1}</span>
                {entityKeys.map((col) => (
                  <Input
                    key={col}
                    addonBefore={col}
                    value={row[col] ?? ''}
                    onChange={(e) => setDebugKeyValue(rowIdx, col, e.target.value)}
                    style={{ width: 220 }}
                    placeholder={`${col} 值`}
                  />
                ))}
                {debugKeys.length > 1 && (
                  <MinusCircleOutlined
                    style={{ color: '#ff4d4f', cursor: 'pointer', fontSize: 16 }}
                    onClick={() => removeDebugRow(rowIdx)}
                  />
                )}
              </Space>
            ))}

            <Space style={{ marginTop: 8 }}>
              <Button
                type="dashed"
                size="small"
                icon={<PlusOutlined />}
                onClick={addDebugRow}
              >
                添加主键行
              </Button>
              <Button
                type="primary"
                icon={<SearchOutlined />}
                loading={debugging}
                onClick={runDebug}
                disabled={!detail.online_enabled}
              >
                查询
              </Button>
            </Space>

            {renderDebugResults()}
          </>
        )}
      </Card>

      {/* Edit Drawer — identical form as FeatureGroups.jsx create drawer */}
      <Drawer
        title={`编辑特征组 — ${detail.name}`}
        placement="right"
        width={680}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => setEditOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="entity_keys" label="主键列" rules={[{ required: true, message: '至少一个主键列' }]}>
            <Select mode="tags" placeholder="输入列名后回车" tokenSeparators={[',']} open={false} />
          </Form.Item>
          <Form.Item name="event_time_col" label="事件时间列">
            <Input placeholder="如: event_time" />
          </Form.Item>
          <Form.Item name="ttl_days" label="TTL（天）">
            <InputNumber min={1} style={{ width: '100%' }} placeholder="不填则不过期" />
          </Form.Item>
          <Form.Item name="online_enabled" label="启用在线服务" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="offline_kind" label="离线落地方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'parquet', label: 'Parquet' },
              { value: 'warehouse', label: 'Warehouse' },
            ]} />
          </Form.Item>
          <Form.Item name="offline_location" label="离线存储路径" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="upstream_tables" label="上游表（血缘）">
            <Select mode="tags" placeholder="输入表名后回车" tokenSeparators={[',']} open={false} />
          </Form.Item>
          <Form.Item name="workflow_id" label="绑定工作流">
            <Select
              allowClear
              placeholder="选择工作流"
              options={workflows.map((w) => ({ value: w.id, label: w.name }))}
              onChange={onWorkflowChange}
            />
          </Form.Item>
          <Form.Item name="task_key" label="绑定节点 Key">
            <Select
              allowClear
              placeholder="先选择工作流"
              disabled={taskKeys.length === 0}
              options={taskKeys.map((k) => ({ value: k, label: k }))}
            />
          </Form.Item>

          <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>特征清单</Typography.Text>
          <Form.List name="features">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...restField }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                    <Form.Item {...restField} name={[name, 'name']} rules={[{ required: true, message: '特征名' }]}>
                      <Input placeholder="特征名" style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item {...restField} name={[name, 'dtype']} initialValue="double">
                      <Select style={{ width: 110 }} options={[
                        { value: 'double', label: 'double' },
                        { value: 'float', label: 'float' },
                        { value: 'int', label: 'int' },
                        { value: 'bigint', label: 'bigint' },
                        { value: 'string', label: 'string' },
                        { value: 'boolean', label: 'boolean' },
                        { value: 'timestamp', label: 'timestamp' },
                      ]} />
                    </Form.Item>
                    <Form.Item {...restField} name={[name, 'description']}>
                      <Input placeholder="口径说明（可选）" style={{ width: 200 }} />
                    </Form.Item>
                    <MinusCircleOutlined
                      style={{ color: '#ff4d4f', cursor: 'pointer' }}
                      onClick={() => remove(name)}
                    />
                  </Space>
                ))}
                <Button
                  type="dashed"
                  onClick={() => add({ name: '', dtype: 'double', description: '' })}
                  block
                  icon={<PlusOutlined />}
                >
                  添加特征
                </Button>
              </>
            )}
          </Form.List>
        </Form>
      </Drawer>
    </div>
  )
}
