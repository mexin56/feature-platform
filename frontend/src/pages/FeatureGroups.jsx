import {
  DeleteOutlined,
  MinusCircleOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import {
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  message,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api.js'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

export default function FeatureGroups() {
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editTarget, setEditTarget] = useState(null) // null = create; object = edit
  const [saving, setSaving] = useState(false)
  const [workflows, setWorkflows] = useState([])
  const [taskKeys, setTaskKeys] = useState([])
  const [collected, setCollected] = useState([]) // 已落库的采集数据集(特征衍生原料)
  const [form] = Form.useForm()
  const navigate = useNavigate()

  useEffect(() => {
    api.get('/api/datasets')
      .then((r) => setCollected((r.items ?? r).filter((d) => d.stats)))
      .catch(() => {})
  }, [])

  const load = () => {
    setLoading(true)
    api.get('/api/feature-groups')
      .then(setGroups)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  const loadWorkflows = () => {
    api.get('/api/workflows').then(setWorkflows).catch(() => {})
  }

  useEffect(() => { load(); loadWorkflows() }, [])

  const onWorkflowChange = async (wid) => {
    form.setFieldValue('task_key', undefined)
    setTaskKeys([])
    if (!wid) return
    try {
      const wf = await api.get(`/api/workflows/${wid}`)
      const nodes = wf.dag?.nodes ?? []
      setTaskKeys(nodes.map((n) => n.key))
    } catch {
      // ignore
    }
  }

  const openCreate = () => {
    setEditTarget(null)
    form.resetFields()
    form.setFieldsValue({
      offline_kind: 'parquet',
      online_enabled: false,
      features: [{ name: '', dtype: 'double', description: '' }],
    })
    setTaskKeys([])
    setDrawerOpen(true)
  }

  const openEdit = async (row) => {
    setEditTarget(row)
    // Fetch full detail (with features + upstream_tables)
    try {
      const detail = await api.get(`/api/feature-groups/${row.id}`)
      // pre-populate task keys if workflow bound
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
      setDrawerOpen(true)
    } catch {
      message.error('加载详情失败')
    }
  }

  const handleSave = async () => {
    let values
    try {
      values = await form.validateFields()
    } catch { return }

    // Normalise entity_keys: antd Select tags mode returns array; allow string too
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
      if (editTarget) {
        await api.put(`/api/feature-groups/${editTarget.id}`, payload)
        message.success('特征组已更新')
      } else {
        await api.post('/api/feature-groups', payload)
        message.success('特征组已创建')
      }
      setDrawerOpen(false)
      load()
    } catch (e) {
      // 409 stale version
      if (e.message && e.message.includes('已有更新版本')) {
        message.warning(e.message)
      }
      // Other errors already shown by api.js
    } finally {
      setSaving(false)
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (v, row) => (
        <a onClick={() => navigate(`/feature-groups/${row.id}`)}>{v}</a>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 70 },
    {
      title: '在线',
      dataIndex: 'online_enabled',
      width: 80,
      render: (v) => <Tag color={v ? 'green' : 'default'}>{v ? '开启' : '关闭'}</Tag>,
    },
    {
      title: '最近产出时间',
      dataIndex: 'last_produced_at',
      render: (v) => fmt(v),
    },
    { title: '行数', dataIndex: 'last_produced_rows', render: (v) => (v != null ? v : '—') },
    {
      title: '物化水位',
      dataIndex: 'materialize_watermark',
      render: (v) => fmt(v),
    },
    {
      title: '操作',
      width: 120,
      render: (_, row) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/feature-groups/${row.id}`)}>详情</Button>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>特征组</Typography.Title>
          <Typography.Text type="secondary">管理特征组版本、在线服务与调度绑定</Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建特征组</Button>
      </div>

      <Table
        rowKey="id"
        loading={loading}
        dataSource={groups}
        columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        size="small"
        locale={{ emptyText: '暂无特征组' }}
      />

      {collected.length > 0 && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '24px 0 8px' }}>
            <div>
              <Typography.Title level={5} style={{ margin: 0 }}>采集数据(特征衍生原料)</Typography.Title>
              <Typography.Text type="secondary">
                已落 market.duckdb 的原始采集表,duckdb_sql 任务与查询页可用 market.表名 访问
              </Typography.Text>
            </div>
            <Button size="small" onClick={() => navigate('/datasets')}>管理采集</Button>
          </div>
          <Table
            rowKey="key"
            dataSource={collected}
            size="small"
            pagination={{ pageSize: 10, hideOnSinglePage: true }}
            columns={[
              { title: '数据集', dataIndex: 'name', width: 200 },
              { title: '来源', dataIndex: 'source', width: 100, render: (v) => <Tag>{v}</Tag> },
              {
                title: '表', dataIndex: 'target_table',
                render: (v) => <Typography.Text code>market.{v}</Typography.Text>,
              },
              { title: '行数', width: 110, render: (_, r) => r.stats?.rows?.toLocaleString() ?? '—' },
              { title: '最新数据日', width: 120, render: (_, r) => r.stats?.max_dt ?? '—' },
              {
                title: '操作', width: 90,
                render: (_, r) => (
                  <Button size="small" type="link" onClick={() => navigate('/query')}>
                    去查询
                  </Button>
                ),
              },
            ]}
          />
        </>
      )}

      <Drawer
        title={editTarget ? `编辑特征组 — ${editTarget.name}` : '新建特征组'}
        placement="right"
        width={680}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          {/* 基本信息 */}
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入特征组名称' }]}>
            <Input placeholder="如: user_credit_features" disabled={!!editTarget} />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>

          {/* 主键列 */}
          <Form.Item
            name="entity_keys"
            label="主键列"
            rules={[{ required: true, message: '至少填一个主键列' }]}
          >
            <Select
              mode="tags"
              placeholder="输入列名后回车,如 user_id"
              tokenSeparators={[',']}
              open={false}
            />
          </Form.Item>

          {/* 事件时间列 */}
          <Form.Item name="event_time_col" label="事件时间列">
            <Input placeholder="如: event_time（启用在线服务时必填）" />
          </Form.Item>

          {/* TTL */}
          <Form.Item name="ttl_days" label="TTL（天）">
            <InputNumber min={1} style={{ width: '100%' }} placeholder="不填则不过期" />
          </Form.Item>

          {/* 在线服务 */}
          <Form.Item name="online_enabled" label="启用在线服务" valuePropName="checked">
            <Switch />
          </Form.Item>

          {/* 离线落地 */}
          <Form.Item name="offline_kind" label="离线落地方式" rules={[{ required: true }]}>
            <Select options={[
              { value: 'parquet', label: 'Parquet' },
              { value: 'warehouse', label: 'Warehouse' },
            ]} />
          </Form.Item>
          <Form.Item name="offline_location" label="离线存储路径" rules={[{ required: true, message: '请填写存储路径' }]}>
            <Input placeholder="如: /data/features/user_credit 或 db.schema.table" />
          </Form.Item>

          {/* 上游表 */}
          <Form.Item name="upstream_tables" label="上游表（血缘）">
            <Select
              mode="tags"
              placeholder="输入表名后回车,如 dwd.user_order"
              tokenSeparators={[',']}
              open={false}
            />
          </Form.Item>

          {/* 绑定工作流 */}
          <Form.Item name="workflow_id" label="绑定工作流（可选）">
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

          {/* 特征清单 */}
          <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>特征清单</Typography.Text>
          <Form.List name="features">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...restField }) => (
                  <Space key={key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                    <Form.Item
                      {...restField}
                      name={[name, 'name']}
                      rules={[{ required: true, message: '特征名' }]}
                    >
                      <Input placeholder="特征名" style={{ width: 160 }} />
                    </Form.Item>
                    <Form.Item
                      {...restField}
                      name={[name, 'dtype']}
                      initialValue="double"
                    >
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
