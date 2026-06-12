/**
 * WorkflowEditor — DAG 编辑器 + 元信息表单
 * 依赖: @xyflow/react (已在 package.json 中声明)
 *
 * 降级方案（若 @xyflow/react 不可用）:
 *   将画布区域替换为节点列表表格 + 边的多选 Select,ECharts graph 做只读预览。
 *   当前使用 @xyflow/react 完整实现。
 */
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  CloseOutlined,
  DeleteOutlined,
  PlusOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import {
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  TimePicker,
  Tooltip,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../api.js'

/* ─── constants ───────────────────────────────────────────────────── */
const NODE_TYPE_OPTIONS = [
  { value: 'duckdb_sql', label: 'DuckDB SQL' },
  { value: 'sql_pushdown', label: 'SQL 下推' },
  { value: 'python_script', label: 'Python 脚本' },
  { value: 'materialize', label: '特征物化' },
  { value: 'dependent', label: '依赖工作流' },
]
const NODE_TYPE_LABEL = Object.fromEntries(NODE_TYPE_OPTIONS.map((o) => [o.value, o.label]))

/* ─── custom node renderer ────────────────────────────────────────── */
import { Handle, Position } from '@xyflow/react'

function DagNode({ data, selected }) {
  return (
    <div
      style={{
        background: selected ? '#e6f4ff' : '#fff',
        border: `2px solid ${selected ? '#1677ff' : '#d9d9d9'}`,
        borderRadius: 8,
        padding: '8px 14px',
        minWidth: 140,
        boxShadow: '0 1px 4px rgba(0,0,0,0.10)',
        cursor: 'pointer',
        position: 'relative',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: '#8c8c8c' }} />
      <div style={{ fontWeight: 600, fontSize: 13, color: '#262626', marginBottom: 2 }}>
        {data.label}
      </div>
      <div style={{ fontSize: 11, color: '#8c8c8c' }}>
        {NODE_TYPE_LABEL[data.nodeType] ?? data.nodeType}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: '#8c8c8c' }} />
    </div>
  )
}

const nodeTypes = { dagNode: DagNode }

/* ─── helpers ──────────────────────────────────────────────────────── */
let _idCounter = 1
const genId = () => `node_${Date.now()}_${_idCounter++}`

function dagToFlow(dag) {
  const nodeMap = {}
  const nodes = (dag.nodes ?? []).map((n, i) => {
    const id = n.key
    nodeMap[id] = n
    return {
      id,
      type: 'dagNode',
      position: { x: (i % 5) * 200, y: Math.floor(i / 5) * 120 },
      data: { label: n.key, nodeType: n.type, dagNode: n },
    }
  })
  const edges = (dag.edges ?? []).map(([src, tgt]) => ({
    id: `${src}->${tgt}`,
    source: src,
    target: tgt,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { strokeWidth: 1.5 },
  }))
  return { nodes, edges }
}

function flowToDag(nodes, edges) {
  return {
    nodes: nodes.map((n) => n.data.dagNode),
    edges: edges.map((e) => [e.source, e.target]),
  }
}

/* ─── type-specific param fields ──────────────────────────────────── */
function ParamsFields({ nodeType, connections, featureGroups, workflows }) {
  if (nodeType === 'duckdb_sql') {
    return (
      <>
        <Form.Item
          name={['params', 'sql']}
          label="SQL"
          extra="支持 {{ ds }} 等模板变量"
        >
          <Input.TextArea rows={5} placeholder="SELECT ..." style={{ fontFamily: 'monospace', fontSize: 12 }} />
        </Form.Item>
        <Form.Item name={['params', 'output_name']} label="输出表名">
          <Input placeholder="如: result_table" />
        </Form.Item>
        <Form.Item name={['params', 'entity_keys']} label="主键列">
          <Select
            mode="tags"
            placeholder="输入列名后回车"
            tokenSeparators={[',']}
            open={false}
          />
        </Form.Item>
      </>
    )
  }

  if (nodeType === 'sql_pushdown') {
    return (
      <>
        <Form.Item
          name={['params', 'connection_id']}
          label="数据源连接"
          rules={[{ required: true, message: '请选择连接' }]}
        >
          <Select
            placeholder="选择连接"
            options={connections.map((c) => ({ value: c.id, label: `${c.name} (${c.conn_type})` }))}
          />
        </Form.Item>
        <Form.Item
          label="SQL"
          extra="可分号分隔多条语句 或 开启多语句模式按行分割"
        >
          <Form.Item name={['params', '_multiline']} valuePropName="checked" noStyle>
            <Switch size="small" />
          </Form.Item>
          <Typography.Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
            多语句模式（按行分割）
          </Typography.Text>
        </Form.Item>
        <Form.Item
          noStyle
          shouldUpdate={(prev, cur) =>
            prev?.params?._multiline !== cur?.params?._multiline
          }
        >
          {({ getFieldValue }) => {
            const multi = getFieldValue(['params', '_multiline'])
            return (
              <Form.Item name={['params', '_sqlText']} label={null}>
                <Input.TextArea
                  rows={5}
                  placeholder={multi ? '每行一条 SQL' : 'SELECT ...'}
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                />
              </Form.Item>
            )
          }}
        </Form.Item>
      </>
    )
  }

  if (nodeType === 'python_script') {
    return (
      <Form.Item name={['params', 'script']} label="脚本路径">
        <Input placeholder="如: scripts/transform.py" />
      </Form.Item>
    )
  }

  if (nodeType === 'materialize') {
    return (
      <Form.Item
        name={['params', 'feature_group_id']}
        label="特征组"
        rules={[{ required: true, message: '请选择特征组' }]}
      >
        <Select
          placeholder="选择特征组"
          options={featureGroups.map((g) => ({ value: g.id, label: g.name }))}
        />
      </Form.Item>
    )
  }

  if (nodeType === 'dependent') {
    return (
      <Form.Item
        name={['params', 'workflow_id']}
        label="依赖工作流"
        rules={[{ required: true, message: '请选择工作流' }]}
      >
        <Select
          placeholder="选择工作流"
          options={workflows.map((w) => ({ value: w.id, label: w.name }))}
        />
      </Form.Item>
    )
  }

  return null
}

/* ─── node config drawer ──────────────────────────────────────────── */
function NodeDrawer({ node, open, onClose, onSave, isNew, connections, featureGroups, workflows }) {
  const [form] = Form.useForm()
  const [nodeType, setNodeType] = useState(node?.data?.nodeType ?? 'duckdb_sql')

  useEffect(() => {
    if (!open) return
    const dagNode = node?.data?.dagNode ?? {}
    const rawType = dagNode.type ?? 'duckdb_sql'
    setNodeType(rawType)

    // Build params — handle sql_pushdown _multiline / _sqlText
    let params = { ...(dagNode.params ?? {}) }
    if (rawType === 'sql_pushdown') {
      const isSqls = Array.isArray(params.sqls)
      params._multiline = isSqls
      params._sqlText = isSqls ? (params.sqls ?? []).join('\n') : (params.sql ?? '')
    }

    form.setFieldsValue({
      key: dagNode.key ?? '',
      type: rawType,
      params,
      retries: dagNode.retries ?? 0,
      retry_delay_sec: dagNode.retry_delay_sec ?? 60,
      timeout_sec: dagNode.timeout_sec ?? undefined,
    })
  }, [open, node])

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }

    // Normalise sql_pushdown params
    let params = { ...(values.params ?? {}) }
    if (values.type === 'sql_pushdown') {
      const multi = params._multiline
      const text = params._sqlText ?? ''
      if (multi) {
        params.sqls = text.split('\n').map((s) => s.trim()).filter(Boolean)
        delete params.sql
      } else {
        params.sql = text
        delete params.sqls
      }
      delete params._multiline
      delete params._sqlText
    }

    onSave({
      key: values.key,
      type: values.type,
      params,
      retries: values.retries ?? 0,
      retry_delay_sec: values.retry_delay_sec ?? 60,
      timeout_sec: values.timeout_sec ?? null,
    })
  }

  return (
    <Drawer
      title={isNew ? '新建节点' : `配置节点 — ${node?.data?.label ?? ''}`}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" onClick={handleSave}>保存节点</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="key"
          label="节点 Key"
          rules={[
            { required: true, message: '请输入节点 Key' },
            { pattern: /^[a-zA-Z_][a-zA-Z0-9_]*$/, message: 'Key 须为字母/数字/下划线,以字母或下划线开头' },
          ]}
        >
          <Input placeholder="如: extract_user" disabled={!isNew} />
        </Form.Item>

        <Form.Item name="type" label="节点类型" rules={[{ required: true }]}>
          <Select
            options={NODE_TYPE_OPTIONS}
            onChange={(v) => setNodeType(v)}
          />
        </Form.Item>

        <ParamsFields
          nodeType={nodeType}
          connections={connections}
          featureGroups={featureGroups}
          workflows={workflows}
        />

        <Typography.Divider />
        <Typography.Text strong style={{ display: 'block', margin: '12px 0 8px' }}>通用参数</Typography.Text>

        <Form.Item name="retries" label="重试次数">
          <InputNumber min={0} max={10} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="retry_delay_sec" label="重试间隔（秒）">
          <InputNumber min={0} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="timeout_sec" label="超时（秒）">
          <InputNumber min={1} style={{ width: '100%' }} placeholder="不填则不限时" />
        </Form.Item>
      </Form>
    </Drawer>
  )
}

/* ─── main component ──────────────────────────────────────────────── */
export default function WorkflowEditor() {
  const { id } = useParams()
  const navigate = useNavigate()
  const isNew = !id || id === 'new'

  const [wfMeta, setWfMeta] = useState(null) // loaded workflow metadata
  const [saving, setSaving] = useState(false)
  const [metaForm] = Form.useForm()

  // React Flow state
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  // Node drawer state
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerNode, setDrawerNode] = useState(null)
  const [drawerIsNew, setDrawerIsNew] = useState(false)

  // Reference data
  const [connections, setConnections] = useState([])
  const [featureGroups, setFeatureGroups] = useState([])
  const [allWorkflows, setAllWorkflows] = useState([])

  const reactFlowWrapper = useRef(null)

  // Load reference data
  useEffect(() => {
    api.get('/api/connections').then(setConnections).catch(() => {})
    api.get('/api/feature-groups').then(setFeatureGroups).catch(() => {})
    api.get('/api/workflows').then((wfs) => {
      // Exclude current workflow from dependent options
      setAllWorkflows(wfs.filter((w) => String(w.id) !== String(id)))
    }).catch(() => {})
  }, [id])

  // Load workflow for edit
  useEffect(() => {
    if (isNew) {
      // default meta
      metaForm.setFieldsValue({
        timezone: 'Asia/Shanghai',
        catchup: false,
        concurrency_limit: 1,
        failure_policy: 'continue',
        alert_on_failure: true,
        alert_on_success: false,
      })
      return
    }
    api.get(`/api/workflows/${id}`).then((wf) => {
      setWfMeta(wf)
      // Set meta form
      metaForm.setFieldsValue({
        name: wf.name,
        description: wf.description,
        cron: wf.cron ?? '',
        timezone: wf.timezone ?? 'Asia/Shanghai',
        catchup: wf.catchup ?? false,
        concurrency_limit: wf.concurrency_limit ?? 1,
        failure_policy: wf.failure_policy ?? 'continue',
        alert_on_failure: wf.alert_on_failure ?? true,
        alert_on_success: wf.alert_on_success ?? false,
        sla_time: wf.sla_time ? dayjs(wf.sla_time, 'HH:mm') : null,
      })
      // Populate DAG
      if (wf.dag) {
        const { nodes: ns, edges: es } = dagToFlow(wf.dag)
        setNodes(ns)
        setEdges(es)
      }
    }).catch(() => { message.error('加载工作流失败') })
  }, [id])

  const onConnect = useCallback(
    (params) =>
      setEdges((eds) =>
        addEdge(
          { ...params, markerEnd: { type: MarkerType.ArrowClosed }, style: { strokeWidth: 1.5 } },
          eds
        )
      ),
    [setEdges]
  )

  // Click on node → open config drawer
  const onNodeClick = useCallback((_evt, node) => {
    setDrawerNode(node)
    setDrawerIsNew(false)
    setDrawerOpen(true)
  }, [])

  // Delete selected nodes with Del key (handled by ReactFlow's deleteKeyCode)
  // Also handle explicit delete button in node (not implemented as custom node button to keep it simple)

  const handleAddNode = () => {
    // Open drawer to configure new node first
    setDrawerNode(null)
    setDrawerIsNew(true)
    setDrawerOpen(true)
  }

  const handleDeleteSelected = () => {
    setNodes((nds) => nds.filter((n) => !n.selected))
    setEdges((eds) => {
      const remainingIds = new Set(
        nodes.filter((n) => !n.selected).map((n) => n.id)
      )
      return eds.filter((e) => remainingIds.has(e.source) && remainingIds.has(e.target))
    })
  }

  const handleNodeSave = (dagNode) => {
    if (drawerIsNew) {
      // Check unique key
      if (nodes.some((n) => n.id === dagNode.key)) {
        message.error(`节点 Key "${dagNode.key}" 已存在`)
        return
      }
      // Auto layout: place in a grid
      const i = nodes.length
      const newNode = {
        id: dagNode.key,
        type: 'dagNode',
        position: { x: (i % 5) * 200, y: Math.floor(i / 5) * 120 },
        data: { label: dagNode.key, nodeType: dagNode.type, dagNode },
      }
      setNodes((nds) => [...nds, newNode])
    } else if (drawerNode) {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === drawerNode.id
            ? { ...n, data: { ...n.data, label: dagNode.key, nodeType: dagNode.type, dagNode } }
            : n
        )
      )
    }
    setDrawerOpen(false)
  }

  const handleSave = async () => {
    let metaValues
    try { metaValues = await metaForm.validateFields() } catch { return }

    const dag = flowToDag(nodes, edges)
    const slaTime = metaValues.sla_time
      ? (dayjs.isDayjs(metaValues.sla_time)
          ? metaValues.sla_time.format('HH:mm')
          : metaValues.sla_time)
      : null

    const payload = {
      name: metaValues.name ?? wfMeta?.name ?? '',
      description: metaValues.description ?? '',
      dag,
      cron: metaValues.cron || null,
      timezone: metaValues.timezone || 'Asia/Shanghai',
      catchup: metaValues.catchup ?? false,
      concurrency_limit: metaValues.concurrency_limit ?? 1,
      failure_policy: metaValues.failure_policy ?? 'continue',
      alert_on_failure: metaValues.alert_on_failure ?? true,
      alert_on_success: metaValues.alert_on_success ?? false,
      sla_time: slaTime,
    }

    setSaving(true)
    try {
      let result
      if (isNew) {
        result = await api.post('/api/workflows', payload)
        message.success(`工作流已创建,版本 v${result.version_no}`)
        navigate(`/workflows/${result.id}`, { replace: true })
      } else {
        result = await api.put(`/api/workflows/${id}`, payload)
        message.success(`已保存,当前版本 v${result.version_no}`)
        setWfMeta(result)
      }
    } catch {
      // shown by api.js
    } finally {
      setSaving(false)
    }
  }

  const selectedCount = nodes.filter((n) => n.selected).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 96px)', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {wfMeta?.name ? `工作流编辑器 — ${wfMeta.name}` : (isNew ? '新建工作流' : `工作流编辑器 #${id}`)}
          </Typography.Title>
          {wfMeta?.version_no != null && (
            <Typography.Text type="secondary">当前版本 v{wfMeta.version_no}</Typography.Text>
          )}
        </div>
        <Space>
          <Button onClick={() => navigate('/workflows')}>返回列表</Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存
          </Button>
        </Space>
      </div>

      {/* DAG Canvas */}
      <div
        ref={reactFlowWrapper}
        style={{
          flex: '0 0 420px',
          border: '1px solid #e8e8e8',
          borderRadius: 8,
          background: '#fafafa',
          overflow: 'hidden',
        }}
      >
        {/* Canvas toolbar */}
        <div style={{
          padding: '6px 12px',
          borderBottom: '1px solid #e8e8e8',
          background: '#fff',
          display: 'flex',
          gap: 8,
          alignItems: 'center',
        }}>
          <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={handleAddNode}>
            添加节点
          </Button>
          <Tooltip title="删除选中节点（或按 Del 键）">
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={selectedCount === 0}
              onClick={handleDeleteSelected}
            >
              删除选中{selectedCount > 0 ? ` (${selectedCount})` : ''}
            </Button>
          </Tooltip>
          <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 4 }}>
            点击节点配置参数 · 拖拽连线建立依赖关系
          </Typography.Text>
        </div>

        <div style={{ height: 360 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            deleteKeyCode="Delete"
            fitView
            fitViewOptions={{ padding: 0.2 }}
          >
            <Background />
            <Controls />
            <MiniMap nodeStrokeWidth={3} zoomable pannable />
          </ReactFlow>
        </div>
      </div>

      {/* Metadata Collapse */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <Collapse
          defaultActiveKey={isNew ? ['meta'] : []}
          items={[
            {
              key: 'meta',
              label: '工作流元信息',
              children: (
                <Form form={metaForm} layout="vertical">
                  {isNew && (
                    <>
                      <Form.Item
                        name="name"
                        label="工作流名称"
                        rules={[{ required: true, message: '请输入名称' }]}
                      >
                        <Input placeholder="如: user_credit_daily" />
                      </Form.Item>
                      <Form.Item name="description" label="描述">
                        <Input.TextArea rows={2} placeholder="可选" />
                      </Form.Item>
                    </>
                  )}

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' }}>
                    <Form.Item
                      name="cron"
                      label="Cron 表达式"
                      extra="如 0 2 * * * 表示每天凌晨 2 点;不填则仅支持手工触发"
                    >
                      <Input placeholder="0 2 * * *" />
                    </Form.Item>
                    <Form.Item name="timezone" label="时区">
                      <Input placeholder="Asia/Shanghai" />
                    </Form.Item>

                    <Form.Item name="concurrency_limit" label="并发上限">
                      <InputNumber min={1} style={{ width: '100%' }} />
                    </Form.Item>
                    <Form.Item name="sla_time" label="SLA 时间">
                      <TimePicker format="HH:mm" style={{ width: '100%' }} placeholder="不设置" />
                    </Form.Item>

                    <Form.Item name="catchup" label="补数追赶 (Catchup)" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                    <Form.Item name="failure_policy" label="失败策略">
                      <Radio.Group>
                        <Radio value="continue">继续</Radio>
                        <Radio value="abort">中止</Radio>
                      </Radio.Group>
                    </Form.Item>

                    <Form.Item name="alert_on_failure" label="失败告警" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                    <Form.Item name="alert_on_success" label="成功告警" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </div>
                </Form>
              ),
            },
          ]}
        />
      </div>

      {/* Node config drawer */}
      <NodeDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSave={handleNodeSave}
        node={drawerNode}
        isNew={drawerIsNew}
        connections={connections}
        featureGroups={featureGroups}
        workflows={allWorkflows}
      />
    </div>
  )
}
