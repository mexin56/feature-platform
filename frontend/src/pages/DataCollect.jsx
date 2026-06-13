import {
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import {
  Button,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
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

/* ── JSON TextArea 解析辅助 ── */
function parseJsonField(value, fieldLabel) {
  if (!value || value.trim() === '') return {}
  try {
    return JSON.parse(value)
  } catch {
    throw new Error(`「${fieldLabel}」不是合法 JSON`)
  }
}

/* ── 自定义数据集 Drawer ── */
function CustomDatasetDrawer({ open, editRecord, onClose, onSuccess }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const [testBusy, setTestBusy] = useState(false)
  const [collectorType, setCollectorType] = useState('http_json')
  const [mode, setMode] = useState('snapshot')
  const [testResult, setTestResult] = useState(null)
  const [testSymbols, setTestSymbols] = useState('000001')
  const [sourceVal, setSourceVal] = useState('')
  const [datasetVal, setDatasetVal] = useState('')

  const isEdit = !!editRecord

  useEffect(() => {
    if (!open) return
    if (isEdit) {
      const cfg = editRecord.config ?? {}
      const ct = editRecord.collector_type ?? 'http_json'
      setCollectorType(ct)
      setMode(editRecord.mode ?? 'snapshot')
      setSourceVal(editRecord.source ?? '')
      setDatasetVal(editRecord.dataset ?? '')
      setTestResult(null)

      const fieldsToSet = {
        source: editRecord.source,
        dataset: editRecord.dataset,
        name: editRecord.name,
        description: editRecord.description ?? editRecord.desc ?? '',
        mode: editRecord.mode ?? 'snapshot',
        collector_type: ct,
      }

      if (ct === 'http_json') {
        fieldsToSet.url = cfg.url ?? ''
        fieldsToSet.method = cfg.method ?? 'GET'
        fieldsToSet.headers = cfg.headers && Object.keys(cfg.headers).length ? JSON.stringify(cfg.headers, null, 2) : ''
        fieldsToSet.params = cfg.params && Object.keys(cfg.params).length ? JSON.stringify(cfg.params, null, 2) : ''
        fieldsToSet.body = cfg.body ? JSON.stringify(cfg.body, null, 2) : ''
        fieldsToSet.records_path = cfg.records_path ?? ''
        fieldsToSet.field_map = cfg.field_map && Object.keys(cfg.field_map).length ? JSON.stringify(cfg.field_map, null, 2) : ''
      } else {
        fieldsToSet.api_name = cfg.api_name ?? ''
        fieldsToSet.tushare_params = cfg.params && Object.keys(cfg.params).length ? JSON.stringify(cfg.params, null, 2) : ''
        fieldsToSet.fields = cfg.fields ?? ''
      }
      form.setFieldsValue(fieldsToSet)
    } else {
      form.resetFields()
      setCollectorType('http_json')
      setMode('snapshot')
      setSourceVal('')
      setDatasetVal('')
      setTestResult(null)
      form.setFieldsValue({ collector_type: 'http_json', mode: 'snapshot', method: 'GET' })
    }
  }, [open, editRecord, isEdit, form])

  const buildConfig = (values) => {
    const ct = values.collector_type
    if (ct === 'http_json') {
      const headers = parseJsonField(values.headers, 'headers')
      const params = parseJsonField(values.params, 'params')
      const bodyVal = values.method === 'POST' ? parseJsonField(values.body, 'body') : undefined
      const field_map = parseJsonField(values.field_map, 'field_map')
      const cfg = {
        url: values.url,
        method: values.method ?? 'GET',
        headers,
        params,
        records_path: values.records_path ?? '',
        field_map,
      }
      if (values.method === 'POST') cfg.body = bodyVal ?? null
      return cfg
    } else {
      const params = parseJsonField(values.tushare_params, 'params')
      return {
        api_name: values.api_name,
        params,
        fields: values.fields ?? '',
      }
    }
  }

  const handleTest = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    let config
    try { config = buildConfig(values) } catch (e) { message.error(e.message); return }

    const symbols = (testSymbols ?? '').split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
    const body = {
      collector_type: values.collector_type,
      config,
      mode: values.mode,
      symbols: symbols.length ? symbols : ['000001'],
    }

    setTestBusy(true)
    setTestResult(null)
    try {
      const r = await api.post('/api/datasets/custom/test', body)
      setTestResult(r)
    } catch {
      // error already shown by api.js
    } finally {
      setTestBusy(false)
    }
  }

  const handleSave = async () => {
    let values
    try { values = await form.validateFields() } catch { return }
    let config
    try { config = buildConfig(values) } catch (e) { message.error(e.message); return }

    setBusy(true)
    try {
      if (isEdit) {
        await api.put(`/api/datasets/custom/${editRecord.id}`, {
          name: values.name,
          description: values.description ?? '',
          mode: values.mode,
          collector_type: values.collector_type,
          config,
        })
        message.success('自定义数据集已更新')
      } else {
        await api.post('/api/datasets/custom', {
          source: values.source,
          dataset: values.dataset,
          name: values.name,
          description: values.description ?? '',
          mode: values.mode,
          collector_type: values.collector_type,
          config,
        })
        message.success('自定义数据集已创建')
      }
      onSuccess()
    } catch {
      // error shown by api.js
    } finally {
      setBusy(false)
    }
  }

  const previewTable = sourceVal && datasetVal
    ? `ods_${sourceVal}_${datasetVal}`
    : '(请先填写来源和数据集标识)'

  const testResultColumns = testResult
    ? testResult.columns.map((c) => ({ title: c, dataIndex: c, key: c, ellipsis: true, width: 120 }))
    : []

  return (
    <Drawer
      title={isEdit ? '编辑自定义数据集' : '新增自定义数据集'}
      open={open}
      onClose={onClose}
      width={720}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={handleTest} loading={testBusy}>测试拉取</Button>
          <Button type="primary" onClick={handleSave} loading={busy}>保存</Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical">
        {/* slug 字段 */}
        <Form.Item label="来源标识 (source)" required style={{ marginBottom: 8 }}>
          <Form.Item
            name="source"
            noStyle
            rules={[
              { required: true, message: '请输入来源标识' },
              { pattern: /^[a-z0-9_]{2,32}$/, message: '须为 ^[a-z0-9_]{2,32}$' },
            ]}
          >
            <Input
              placeholder="如 my_source（^[a-z0-9_]{2,32}$）"
              disabled={isEdit}
              onChange={(e) => setSourceVal(e.target.value)}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form.Item>

        <Form.Item label="数据集标识 (dataset)" required style={{ marginBottom: 8 }}>
          <Form.Item
            name="dataset"
            noStyle
            rules={[
              { required: true, message: '请输入数据集标识' },
              { pattern: /^[a-z0-9_]{2,32}$/, message: '须为 ^[a-z0-9_]{2,32}$' },
            ]}
          >
            <Input
              placeholder="如 daily_price（^[a-z0-9_]{2,32}$）"
              disabled={isEdit}
              onChange={(e) => setDatasetVal(e.target.value)}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Form.Item>

        <Form.Item label="目标表预览">
          <Typography.Text code>{previewTable}</Typography.Text>
        </Form.Item>

        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="数据集中文名称" />
        </Form.Item>

        <Form.Item name="description" label="说明">
          <Input.TextArea rows={2} placeholder="可选说明" />
        </Form.Item>

        <Form.Item name="mode" label="采集模式">
          <Radio.Group onChange={(e) => setMode(e.target.value)}>
            <Radio value="snapshot">快照 (snapshot)</Radio>
            <Radio value="per_symbol">逐股 (per_symbol)</Radio>
          </Radio.Group>
        </Form.Item>

        <Form.Item name="collector_type" label="采集器类型">
          <Radio.Group onChange={(e) => setCollectorType(e.target.value)}>
            <Radio value="http_json">HTTP JSON</Radio>
            <Radio value="tushare_api">tushare 通用</Radio>
          </Radio.Group>
        </Form.Item>

        <Divider style={{ margin: '12px 0' }} />

        {collectorType === 'http_json' && (
          <>
            <Form.Item
              name="url"
              label="URL"
              rules={[{ required: true, message: '请输入 URL' }]}
              extra="支持占位符：{dt}（YYYYMMDD）、{dt_nodash}（YYYYMMDD 无连字符）、{symbol}"
            >
              <Input.TextArea rows={2} placeholder="https://example.com/api?date={dt}" />
            </Form.Item>

            <Form.Item name="method" label="Method">
              <Select style={{ width: 120 }} options={[{ value: 'GET' }, { value: 'POST' }]} />
            </Form.Item>

            <Form.Item
              name="headers"
              label="Headers（JSON）"
              extra="解析失败时会提示错误，留空视为 {}"
            >
              <Input.TextArea rows={3} placeholder='{"Authorization": "Bearer token"}' />
            </Form.Item>

            <Form.Item
              name="params"
              label="Query Params（JSON）"
              extra="留空视为 {}"
            >
              <Input.TextArea rows={3} placeholder='{"page": 1}' />
            </Form.Item>

            {mode === 'snapshot' ? null : null /* body shown by method watch below */}
            <Form.Item
              noStyle
              shouldUpdate={(prev, cur) => prev.method !== cur.method}
            >
              {({ getFieldValue }) =>
                getFieldValue('method') === 'POST' ? (
                  <Form.Item
                    name="body"
                    label="Request Body（JSON）"
                    extra="留空视为 null"
                  >
                    <Input.TextArea rows={3} placeholder='{"key": "value"}' />
                  </Form.Item>
                ) : null
              }
            </Form.Item>

            <Form.Item
              name="records_path"
              label="Records Path"
              extra="JSON 响应中数组的点路径，如 data.list；留空取响应根节点"
            >
              <Input placeholder="data.list" />
            </Form.Item>

            <Form.Item
              name="field_map"
              label="Field Map（JSON）"
              extra="列名 → JSON 键 的映射，留空则取首条记录所有字段"
            >
              <Input.TextArea rows={3} placeholder='{"close": "closePrice", "volume": "vol"}' />
            </Form.Item>
          </>
        )}

        {collectorType === 'tushare_api' && (
          <>
            <Form.Item
              name="api_name"
              label="API 名称"
              rules={[{ required: true, message: '请输入 tushare API 名称' }]}
            >
              <Input placeholder="daily / fina_indicator / …" />
            </Form.Item>

            <Form.Item
              name="tushare_params"
              label="Params（JSON）"
              extra="逐股模式下 {symbol} 占位符会自动代入归一化 ts_code，留空视为 {}"
            >
              <Input.TextArea rows={3} placeholder='{"trade_date": "{dt}"}' />
            </Form.Item>

            <Form.Item
              name="fields"
              label="Fields（逗号分隔）"
              extra="留空则取全部字段"
            >
              <Input placeholder="ts_code,trade_date,close,vol" />
            </Form.Item>
          </>
        )}

        {/* 测试拉取区 */}
        <Divider style={{ margin: '12px 0' }}>测试拉取</Divider>
        <Space style={{ marginBottom: 12 }} wrap>
          {mode === 'per_symbol' && (
            <Form.Item label="测试股票代码（逗号/空格分隔）" style={{ marginBottom: 0 }}>
              <Input
                value={testSymbols}
                onChange={(e) => setTestSymbols(e.target.value)}
                style={{ width: 220 }}
                placeholder="000001"
              />
            </Form.Item>
          )}
          <Form.Item style={{ marginBottom: 0 }}>
            <Button onClick={handleTest} loading={testBusy}>执行测试</Button>
          </Form.Item>
        </Space>

        {testResult && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              共 {testResult.row_count} 行，展示前 {testResult.rows.length} 行
            </Typography.Text>
            <Table
              size="small"
              columns={testResultColumns}
              dataSource={testResult.rows.map((r, i) => ({ ...r, __key: i }))}
              rowKey="__key"
              pagination={false}
              scroll={{ x: 'max-content' }}
              style={{ marginTop: 8 }}
            />
          </div>
        )}
      </Form>
    </Drawer>
  )
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

  /* Custom dataset drawer */
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editRecord, setEditRecord] = useState(null)

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
        && !(ds.desc ?? ds.description ?? '').toLowerCase().includes(kw)) return false
      if (onlyAvailable && !ds.custom && !ds.available) return false
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

  /* ── Delete custom dataset ── */
  const handleDeleteCustom = async (record) => {
    try {
      await api.del(`/api/datasets/custom/${record.id}`)
      message.success(`已删除数据集 ${record.name}`)
      load()
    } catch {
      // error shown by api.js
    }
  }

  /* ── Open drawer for new / edit ── */
  const openNewDrawer = () => {
    setEditRecord(null)
    setDrawerOpen(true)
  }

  const openEditDrawer = (record) => {
    setEditRecord(record)
    setDrawerOpen(true)
  }

  const handleDrawerSuccess = () => {
    setDrawerOpen(false)
    setEditRecord(null)
    load()
  }

  /* ── Table columns ── */
  const columns = [
    {
      title: '数据集',
      width: 240,
      render: (_, row) => (
        <Space direction="vertical" size={0}>
          <Space size={4}>
            <Typography.Text strong style={{ fontSize: 13 }}>{row.name}</Typography.Text>
            {row.custom && <Tag color="purple" style={{ marginLeft: 2 }}>自定义</Tag>}
          </Space>
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
      ellipsis: true,
      render: (_, row) => {
        const v = row.desc ?? row.description ?? ''
        return (
          <Tooltip title={v}>
            <span>{v}</span>
          </Tooltip>
        )
      },
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
      width: 80,
      render: (_, row) => {
        if (row.custom || row.available) {
          return <Tag color="success">可用</Tag>
        }
        return (
          <Tooltip title={row.reason}>
            <Tag color="error">不可用</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      width: 100,
      render: (_, row) => {
        if (!row.custom) return null
        return (
          <Space size={4}>
            <Tooltip title="编辑">
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={() => openEditDrawer(row)}
              />
            </Tooltip>
            <Popconfirm
              title="确认删除该自定义数据集？"
              okText="删除"
              okType="danger"
              cancelText="取消"
              onConfirm={() => handleDeleteCustom(row)}
            >
              <Tooltip title="删除">
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                />
              </Tooltip>
            </Popconfirm>
          </Space>
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
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openNewDrawer}
          >
            新增自定义数据集
          </Button>
          {isAdmin && (
            <Button
              size="small"
              icon={<DatabaseOutlined />}
              onClick={openTokenModal}
            >
              tushare Token
            </Button>
          )}
        </Space>
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
          getCheckboxProps: (row) => ({ disabled: row.custom ? false : !row.available }),
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

      {/* Custom Dataset Drawer */}
      <CustomDatasetDrawer
        open={drawerOpen}
        editRecord={editRecord}
        onClose={() => { setDrawerOpen(false); setEditRecord(null) }}
        onSuccess={handleDrawerSuccess}
      />

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
