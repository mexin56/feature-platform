import {
  CheckCircleOutlined,
  DeleteOutlined,
  DisconnectOutlined,
  KeyOutlined,
  PlusOutlined,
  StopOutlined,
  WifiOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'

import { api } from '../api.js'

/* ─── constants ─────────────────────────────────────────────────── */
const ROLE_LABEL = { admin: '管理员', developer: '开发者', viewer: '只读' }
const ROLE_OPTIONS = Object.entries(ROLE_LABEL).map(([k, v]) => ({ value: k, label: v }))
const CONN_TYPE_OPTIONS = [
  { value: 'mysql', label: 'MySQL' },
  { value: 'spark', label: 'Spark' },
]
const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

/* ══════════════════════════════════════════════════════════════════
   Tab 1: 用户管理
══════════════════════════════════════════════════════════════════ */
function UsersTab() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(null) // user object
  const [form] = Form.useForm()
  const [resetForm] = Form.useForm()

  const load = () => {
    setLoading(true)
    api.get('/api/users').then(setUsers).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    const v = await form.validateFields()
    try {
      await api.post('/api/users', v)
      message.success('用户已创建')
      setCreateOpen(false)
      form.resetFields()
      load()
    } catch { /* shown by api.js */ }
  }

  const handleReset = async () => {
    const v = await resetForm.validateFields()
    try {
      await api.post(`/api/users/${resetOpen.id}/reset-password`, { new_password: v.new_password })
      message.success('密码已重置')
      setResetOpen(null)
      resetForm.resetFields()
    } catch { /* shown */ }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '角色', dataIndex: 'role', width: 150,
      render: (v, r) => (
        <Select
          size="small"
          value={v}
          style={{ width: 120 }}
          options={ROLE_OPTIONS}
          onChange={async (role) => {
            try { await api.patch(`/api/users/${r.id}`, { role }); load() } catch { /* shown */ }
          }}
        />
      ),
    },
    {
      title: '启用', dataIndex: 'is_active', width: 80,
      render: (v, r) => (
        <Switch
          checked={v}
          onChange={async (checked) => {
            try { await api.patch(`/api/users/${r.id}`, { is_active: checked }); load() } catch { /* shown */ }
          }}
        />
      ),
    },
    { title: '创建时间', dataIndex: 'created_at', render: fmt },
    {
      title: '操作', key: 'action', width: 110,
      render: (_, r) => (
        <Button size="small" icon={<KeyOutlined />} onClick={() => { setResetOpen(r); resetForm.resetFields() }}>
          重置密码
        </Button>
      ),
    },
  ]

  return (
    <>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建用户</Button>
        <Table rowKey="id" loading={loading} dataSource={users} columns={columns} pagination={false} />
      </Space>

      <Modal title="新建用户" open={createOpen} onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); form.resetFields() }} destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ role: 'developer' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input autoFocus /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6, message: '至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重置密码 — ${resetOpen?.username}`}
        open={!!resetOpen}
        onOk={handleReset}
        onCancel={() => { setResetOpen(null); resetForm.resetFields() }}
        destroyOnClose
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6, message: '至少 6 位' }]}>
            <Input.Password autoFocus />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

/* ══════════════════════════════════════════════════════════════════
   Tab 2: 连接管理
══════════════════════════════════════════════════════════════════ */
function ConnectionsTab() {
  const [conns, setConns] = useState([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editConn, setEditConn] = useState(null)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()

  const load = () => {
    setLoading(true)
    api.get('/api/connections').then(setConns).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    const v = await form.validateFields()
    try {
      await api.post('/api/connections', v)
      message.success('连接已创建')
      setCreateOpen(false)
      form.resetFields()
      load()
    } catch { /* shown */ }
  }

  const handleEdit = async () => {
    const v = await editForm.validateFields()
    // only send non-empty fields
    const patch = {}
    for (const k of ['host', 'port', 'username', 'password', 'database']) {
      if (v[k] !== undefined && v[k] !== '') patch[k] = v[k]
    }
    try {
      await api.patch(`/api/connections/${editConn.id}`, patch)
      message.success('已更新')
      setEditConn(null)
      editForm.resetFields()
      load()
    } catch { /* shown */ }
  }

  const handleDelete = async (id) => {
    try {
      await api.del(`/api/connections/${id}`)
      message.success('已删除')
      load()
    } catch { /* shown */ }
  }

  const handleTest = async (id) => {
    const hide = message.loading('测试中…', 0)
    try {
      await api.post(`/api/connections/${id}/test`)
      hide()
      message.success('连接成功')
    } catch {
      hide()
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'conn_type', width: 80, render: (v) => <Tag>{v}</Tag> },
    { title: '主机', dataIndex: 'host' },
    { title: '端口', dataIndex: 'port', width: 70 },
    { title: '用户', dataIndex: 'username' },
    { title: '数据库', dataIndex: 'database' },
    {
      title: '操作', key: 'action', width: 200,
      render: (_, r) => (
        <Space size="small">
          <Button size="small" icon={<WifiOutlined />} onClick={() => handleTest(r.id)}>测试</Button>
          <Button size="small" onClick={() => {
            setEditConn(r)
            editForm.setFieldsValue({ host: r.host, port: r.port, username: r.username, database: r.database })
          }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => Modal.confirm({
              title: `确认删除连接「${r.name}」?`,
              onOk: () => handleDelete(r.id),
            })}>
            删除
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建连接</Button>
        <Table rowKey="id" loading={loading} dataSource={conns} columns={columns}
          pagination={false} scroll={{ x: 800 }} />
      </Space>

      <Modal title="新建连接" open={createOpen} onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); form.resetFields() }} destroyOnClose width={520}>
        <Form form={form} layout="vertical" initialValues={{ port: 3306, conn_type: 'mysql' }}>
          <Form.Item name="name" label="连接名称" rules={[{ required: true }]}><Input autoFocus /></Form.Item>
          <Form.Item name="conn_type" label="类型" rules={[{ required: true }]}>
            <Select options={CONN_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="host" label="主机" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="port" label="端口" rules={[{ required: true }]}>
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="username" label="用户名"><Input /></Form.Item>
          <Form.Item name="password" label="密码"><Input.Password /></Form.Item>
          <Form.Item name="database" label="数据库"><Input /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`编辑连接 — ${editConn?.name}`} open={!!editConn} onOk={handleEdit}
        onCancel={() => { setEditConn(null); editForm.resetFields() }} destroyOnClose width={520}>
        <Form form={editForm} layout="vertical">
          <Form.Item name="host" label="主机"><Input /></Form.Item>
          <Form.Item name="port" label="端口"><InputNumber style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="username" label="用户名"><Input /></Form.Item>
          <Form.Item name="password" label="密码(留空不修改)"><Input.Password /></Form.Item>
          <Form.Item name="database" label="数据库"><Input /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}

/* ══════════════════════════════════════════════════════════════════
   Tab 3: API Key 管理
══════════════════════════════════════════════════════════════════ */
function ApiKeysTab() {
  const [keys, setKeys] = useState([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [newKey, setNewKey] = useState(null) // plaintext after creation
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    api.get('/api/api-keys').then(setKeys).catch(() => {}).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    const v = await form.validateFields()
    try {
      const result = await api.post('/api/api-keys', v)
      setCreateOpen(false)
      form.resetFields()
      setNewKey(result.key) // show plaintext once
      load()
    } catch { /* shown */ }
  }

  const handleDisable = async (id) => {
    try {
      await api.post(`/api/api-keys/${id}/disable`)
      message.success('已禁用')
      load()
    } catch { /* shown */ }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    {
      title: '状态', dataIndex: 'is_active', width: 80,
      render: (v) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '禁用'}</Tag>,
    },
    { title: '调用次数', dataIndex: 'calls', width: 90 },
    { title: '创建时间', dataIndex: 'created_at', render: fmt },
    {
      title: '操作', key: 'action', width: 100,
      render: (_, r) =>
        r.is_active ? (
          <Button size="small" danger icon={<StopOutlined />}
            onClick={() => Modal.confirm({
              title: `确认禁用 API Key「${r.name}」?`,
              content: '禁用后无法恢复启用,如需使用请重新创建。',
              onOk: () => handleDisable(r.id),
            })}>
            禁用
          </Button>
        ) : (
          <Tag color="default" icon={<DisconnectOutlined />}>已禁用</Tag>
        ),
    },
  ]

  return (
    <>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>创建 API Key</Button>
        <Table rowKey="id" loading={loading} dataSource={keys} columns={columns} pagination={false} />
      </Space>

      {/* 创建 Modal */}
      <Modal title="创建 API Key" open={createOpen} onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); form.resetFields() }} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input autoFocus /></Form.Item>
        </Form>
      </Modal>

      {/* 明文展示 Modal — 仅此一次 */}
      <Modal
        title={<Space><CheckCircleOutlined style={{ color: '#52c41a' }} />API Key 已创建</Space>}
        open={!!newKey}
        onOk={() => setNewKey(null)}
        onCancel={() => setNewKey(null)}
        cancelButtonProps={{ style: { display: 'none' } }}
        okText="我已复制,关闭"
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          message="仅此一次展示明文 Key,关闭后无法再次查看,请立即复制保存!"
          style={{ marginBottom: 12 }}
        />
        <Input.TextArea
          value={newKey}
          readOnly
          autoSize
          style={{ fontFamily: 'monospace', fontSize: 13 }}
          onClick={(e) => e.target.select()}
        />
      </Modal>
    </>
  )
}

/* ══════════════════════════════════════════════════════════════════
   Tab 4: 系统设置(Webhook)
══════════════════════════════════════════════════════════════════ */
function SettingsTab() {
  const [webhookUrl, setWebhookUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.get('/api/settings/webhook_url')
      .then((r) => setWebhookUrl(r.value ?? ''))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put('/api/settings/webhook_url', { value: webhookUrl })
      message.success('Webhook 地址已保存')
    } catch { /* shown */ } finally {
      setSaving(false)
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%', maxWidth: 600 }}>
      <Typography.Text strong>告警 Webhook 地址</Typography.Text>
      <Typography.Text type="secondary">
        调度失败、SLA 超时等告警将以 HTTP POST 推送至此地址(JSON 格式)。
      </Typography.Text>
      <Input
        placeholder="https://example.com/webhook"
        value={webhookUrl}
        onChange={(e) => setWebhookUrl(e.target.value)}
        disabled={loading}
        style={{ width: '100%' }}
      />
      <Button type="primary" onClick={handleSave} loading={saving}>保存</Button>
    </Space>
  )
}

/* ══════════════════════════════════════════════════════════════════
   Admin page — admin role only (App.jsx guards the route)
══════════════════════════════════════════════════════════════════ */
export default function Admin() {
  const tabs = [
    { key: 'users', label: '用户管理', children: <UsersTab /> },
    { key: 'connections', label: '连接管理', children: <ConnectionsTab /> },
    { key: 'apikeys', label: 'API Key', children: <ApiKeysTab /> },
    { key: 'settings', label: 'Webhook 设置', children: <SettingsTab /> },
  ]

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>系统管理</Typography.Title>
        <Typography.Text type="secondary">用户 / 连接 / API Key / Webhook(仅管理员可见)</Typography.Text>
      </div>
      <Card>
        <Tabs items={tabs} defaultActiveKey="users" />
      </Card>
    </div>
  )
}
