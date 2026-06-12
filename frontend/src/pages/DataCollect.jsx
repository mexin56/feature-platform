import {
  DatabaseOutlined,
} from '@ant-design/icons'
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api.js'

/* ── 来源中文名 ── */
const SOURCE_LABELS = {
  tushare: 'Tushare',
  akshare: 'AkShare',
  baostock: 'BaoStock',
  tencent: '腾讯财经',
  ths: '同花顺',
  eastmoney: '东方财富',
  mootdx: '通达信',
  sina: '新浪财经',
  cninfo: '巨潮',
  qmt: 'QMT',
}

/* 来源 Tag 固定颜色 */
const SOURCE_COLORS = {
  tushare: 'purple',
  akshare: 'cyan',
  baostock: 'geekblue',
  tencent: 'blue',
  ths: 'red',
  eastmoney: 'orange',
  mootdx: 'gold',
  sina: 'volcano',
  cninfo: 'lime',
  qmt: 'default',
}

/* 默认工作流名称 */
function defaultWorkflowName() {
  const d = new Date()
  const ymd = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `数据采集_${ymd}`
}

export default function DataCollect() {
  const navigate = useNavigate()
  const [datasets, setDatasets] = useState([])
  const [loading, setLoading] = useState(false)

  /* Filters */
  const [filterSource, setFilterSource] = useState('__all__')
  const [filterMode, setFilterMode] = useState('__all__')
  const [filterKeyword, setFilterKeyword] = useState('')
  const [onlyAvailable, setOnlyAvailable] = useState(false)

  /* Row selection */
  const [selectedKeys, setSelectedKeys] = useState([])

  /* Workflow modal */
  const [wfModalOpen, setWfModalOpen] = useState(false)
  const [wfBusy, setWfBusy] = useState(false)
  const [wfForm] = Form.useForm()

  /* tushare token modal */
  const [tokenModalOpen, setTokenModalOpen] = useState(false)
  const [tokenBusy, setTokenBusy] = useState(false)
  const [tokenForm] = Form.useForm()

  /* admin detection — read from localStorage via App auth (stored in closure) */
  const [isAdmin, setIsAdmin] = useState(false)

  useEffect(() => {
    /* Detect admin from /api/auth/me (already fetched by App; re-use cheaply) */
    api.get('/api/auth/me')
      .then((r) => { const u = r.user ?? r; setIsAdmin(u.role === 'admin') })
      .catch(() => {})
  }, [])

  const load = () => {
    setLoading(true)
    api.get('/api/datasets')
      .then(setDatasets)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  /* ── Filtered rows ── */
  const filtered = useMemo(() => {
    const kw = filterKeyword.trim().toLowerCase()
    return datasets.filter((ds) => {
      if (filterSource !== '__all__' && ds.source !== filterSource) return false
      if (filterMode !== '__all__' && ds.mode !== filterMode) return false
      if (kw && !ds.key.toLowerCase().includes(kw) && !ds.name.toLowerCase().includes(kw)
        && !(ds.desc ?? '').toLowerCase().includes(kw)) return false
      if (onlyAvailable && !ds.available) return false
      return true
    })
  }, [datasets, filterSource, filterMode, filterKeyword, onlyAvailable])

  /* Selected datasets (full objects) */
  const selectedDatasets = useMemo(
    () => datasets.filter((ds) => selectedKeys.includes(ds.key)),
    [datasets, selectedKeys],
  )
  const hasPerSymbol = selectedDatasets.some((ds) => ds.mode === 'per_symbol')

  /* ── tushare Token Modal handlers ── */
  const openTokenModal = async () => {
    tokenForm.resetFields()
    setTokenModalOpen(true)
    try {
      const r = await api.get('/api/settings/tushare_token')
      tokenForm.setFieldsValue({ token: r.value || '' })
    } catch {
      // non-admin blocked: modal will show empty
    }
  }

  const saveToken = async () => {
    let values
    try { values = await tokenForm.validateFields() } catch { return }
    setTokenBusy(true)
    try {
      await api.put('/api/settings/tushare_token', { value: values.token })
      message.success('Tushare Token 已保存')
      setTokenModalOpen(false)
      load() // refresh availability
    } catch {
      // error shown by api.js
    } finally {
      setTokenBusy(false)
    }
  }

  /* ── Workflow Modal handlers ── */
  const openWfModal = () => {
    wfForm.resetFields()
    wfForm.setFieldsValue({
      name: defaultWorkflowName(),
      cron: '0 17 * * 1-5',
      interval_sec: 0.5,
    })
    setWfModalOpen(true)
  }

  const submitWf = async () => {
    let values
    try { values = await wfForm.validateFields() } catch { return }

    const symbols = hasPerSymbol
      ? (values.symbols ?? '').split('\n').map((s) => s.trim()).filter(Boolean)
      : []

    if (hasPerSymbol && symbols.length === 0) {
      message.error('含逐股数据集,请填写股票代码')
      return
    }

    setWfBusy(true)
    try {
      const body = {
        name: values.name,
        cron: values.cron,
        dataset_keys: selectedKeys,
        symbols,
        interval_sec: values.interval_sec ?? 0.5,
      }
      const r = await api.post('/api/datasets/seed-workflow', body)
      message.success(`工作流已创建 (id=${r.id},共 ${r.task_count} 个任务)`)
      setWfModalOpen(false)
      setSelectedKeys([])
      navigate(`/workflows/${r.id}`)
    } catch {
      // error shown by api.js
    } finally {
      setWfBusy(false)
    }
  }

  /* ── Table columns ── */
  const columns = [
    {
      title: '数据集',
      width: 220,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong style={{ fontSize: 13 }}>{row.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>{row.key}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      render: (v) => (
        <Tag color={SOURCE_COLORS[v] ?? 'default'}>{SOURCE_LABELS[v] ?? v}</Tag>
      ),
    },
    {
      title: '模块',
      dataIndex: 'module',
      width: 110,
      ellipsis: true,
    },
    {
      title: '模式',
      dataIndex: 'mode',
      width: 80,
      render: (v) => (
        v === 'per_symbol'
          ? <Tag color="orange">逐股</Tag>
          : <Tag color="blue">快照</Tag>
      ),
    },
    {
      title: '说明',
      dataIndex: 'desc',
      ellipsis: true,
      render: (v) => (
        <Tooltip title={v}>
          <span>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '目标表',
      dataIndex: 'target_table',
      width: 200,
      render: (v) => (
        <Typography.Text code style={{ fontSize: 11 }}>{v}</Typography.Text>
      ),
    },
    {
      title: '已采集',
      width: 160,
      render: (_, row) => {
        const s = row.stats
        if (!s) return <Typography.Text type="secondary">—</Typography.Text>
        return (
          <Typography.Text style={{ fontSize: 12 }}>
            {s.rows} 行 / {s.max_dt ?? '—'}
          </Typography.Text>
        )
      },
    },
    {
      title: '状态',
      width: 100,
      render: (_, row) => {
        if (row.available) {
          return <Tag color="success">可用</Tag>
        }
        return (
          <Tooltip title={row.reason}>
            <Tag color="error">不可用</Tag>
          </Tooltip>
        )
      },
    },
  ]

  /* ── Render ── */
  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>数据采集</Typography.Title>
          <Typography.Text type="secondary">
            开源金融数据目录，调度入 market.duckdb，查询页/特征衍生可用 market.ods_* 访问
          </Typography.Text>
        </div>
        {isAdmin && (
          <Button
            size="small"
            icon={<DatabaseOutlined />}
            onClick={openTokenModal}
          >
            tushare Token
          </Button>
        )}
      </div>

      {/* Filters */}
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          style={{ width: 130 }}
          value={filterSource}
          onChange={setFilterSource}
          options={[
            { value: '__all__', label: '全部来源' },
            ...Object.entries(SOURCE_LABELS).map(([k, v]) => ({ value: k, label: v })),
          ]}
        />
        <Select
          style={{ width: 120 }}
          value={filterMode}
          onChange={setFilterMode}
          options={[
            { value: '__all__', label: '全部模式' },
            { value: 'snapshot', label: '快照' },
            { value: 'per_symbol', label: '逐股' },
          ]}
        />
        <Input.Search
          placeholder="关键词搜索 key/名称/说明"
          allowClear
          style={{ width: 240 }}
          onSearch={setFilterKeyword}
          onChange={(e) => { if (!e.target.value) setFilterKeyword('') }}
        />
        <Space>
          <Switch
            size="small"
            checked={onlyAvailable}
            onChange={setOnlyAvailable}
          />
          <Typography.Text>仅看可用</Typography.Text>
        </Space>
      </Space>

      {/* Table */}
      <Table
        rowKey="key"
        loading={loading}
        dataSource={filtered}
        columns={columns}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        rowSelection={{
          type: 'checkbox',
          selectedRowKeys: selectedKeys,
          onChange: (keys) => setSelectedKeys(keys),
          getCheckboxProps: (row) => ({ disabled: !row.available }),
        }}
        locale={{ emptyText: '暂无数据集' }}
      />

      {/* Footer bar */}
      <div style={{
        position: 'sticky',
        bottom: 0,
        background: '#fff',
        borderTop: '1px solid #eaecf0',
        padding: '10px 0',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        marginTop: 8,
      }}>
        <Typography.Text type="secondary">
          已选 <strong>{selectedKeys.length}</strong> 个
        </Typography.Text>
        <Button
          type="primary"
          disabled={selectedKeys.length === 0}
          onClick={openWfModal}
        >
          生成采集工作流
        </Button>
      </div>

      {/* tushare Token Modal */}
      <Modal
        title="Tushare Token 配置"
        open={tokenModalOpen}
        onCancel={() => setTokenModalOpen(false)}
        onOk={saveToken}
        confirmLoading={tokenBusy}
        destroyOnClose
      >
        <Form form={tokenForm} layout="vertical">
          <Form.Item
            name="token"
            label="Token"
            rules={[{ required: true, message: '请输入 Tushare Token' }]}
          >
            <Input.Password placeholder="请输入 tushare pro token" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Seed Workflow Modal */}
      <Modal
        title="生成采集工作流"
        open={wfModalOpen}
        onCancel={() => setWfModalOpen(false)}
        onOk={submitWf}
        confirmLoading={wfBusy}
        destroyOnClose
        width={540}
      >
        <Form form={wfForm} layout="vertical">
          <Form.Item
            name="name"
            label="工作流名称"
            rules={[{ required: true, message: '请输入工作流名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="cron"
            label="Cron 表达式"
            extra="默认为工作日 17:00（0 17 * * 1-5）"
            rules={[{ required: true, message: '请输入 cron 表达式' }]}
          >
            <Input placeholder="0 17 * * 1-5" />
          </Form.Item>

          {hasPerSymbol && (
            <>
              <Form.Item
                name="symbols"
                label="股票代码池（每行一个 6 位代码）"
                rules={[{ required: true, message: '含逐股数据集，请填写股票代码' }]}
              >
                <Input.TextArea
                  rows={5}
                  placeholder={'000001\n000002\n600000'}
                />
              </Form.Item>
              <Form.Item
                name="interval_sec"
                label="逐股采集间隔（秒）"
              >
                <InputNumber min={0} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  )
}
