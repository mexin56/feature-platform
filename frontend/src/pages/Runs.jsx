import {
  CheckCircleOutlined,
  EyeOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  Button,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { api } from '../api.js'
import StateTag from '../components/StateTag.jsx'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

const RUN_TYPE_COLOR = {
  manual: 'blue',
  scheduled: 'green',
  backfill: 'gold',
}

export default function Runs() {
  const [workflows, setWorkflows] = useState([])
  const [selectedWid, setSelectedWid] = useState(null)
  const [runs, setRuns] = useState([])
  const [loadingWf, setLoadingWf] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [stateFilter, setStateFilter] = useState(null)
  const [typeFilter, setTypeFilter] = useState(null)
  const [pageSize, setPageSize] = useState(20)
  const [actionBusy, setActionBusy] = useState({})
  const [selectedRowKeys, setSelectedRowKeys] = useState([])
  const [batchStopping, setBatchStopping] = useState(false)
  const navigate = useNavigate()

  // Ref avoids stale closure in setInterval polling
  const filterRef = useRef({ wid: null, state: null, type: null })
  const syncFilter = (wid, state, type) => {
    filterRef.current = { wid, state, type }
  }

  // Load workflow list once on mount
  useEffect(() => {
    setLoadingWf(true)
    api.get('/api/workflows')
      .then((list) => setWorkflows(list))
      .catch(() => {})
      .finally(() => setLoadingWf(false))
  }, [])

  const buildRunsUrl = (wid, state, type) => {
    const params = new URLSearchParams()
    if (wid)   params.set('workflow_id', wid)
    if (state) params.set('state', state)
    if (type)  params.set('run_type', type)
    const qs = params.toString()
    return qs ? `/api/runs?${qs}` : '/api/runs'
  }

  const fetchRuns = (wid, state, type) => {
    api.get(buildRunsUrl(wid, state, type))
      .then(setRuns)
      .catch(() => {})
  }

  // Fetch runs whenever any filter changes
  useEffect(() => {
    setLoadingRuns(true)
    fetchRuns(selectedWid, stateFilter, typeFilter)
    // Use setTimeout to avoid setState-during-render warning
    const t = setTimeout(() => setLoadingRuns(false), 50)
    return () => clearTimeout(t)
  }, [selectedWid, stateFilter, typeFilter])

  // 5s polling — read latest filter from ref to avoid stale closure
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      const { wid, state, type } = filterRef.current
      fetchRuns(wid, state, type)
    }, 5000)
    return () => clearInterval(id)
  }, [])

  const handleStop = async (rid) => {
    setActionBusy((b) => ({ ...b, [rid]: true }))
    try {
      await api.post(`/api/runs/${rid}/stop`)
      message.success('实例已终止')
      fetchRuns(selectedWid, stateFilter, typeFilter)
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, [rid]: false }))
    }
  }

  const handleRetry = async (rid) => {
    setActionBusy((b) => ({ ...b, [rid]: true }))
    try {
      await api.post(`/api/runs/${rid}/retry`)
      message.success('实例已重跑')
      fetchRuns(selectedWid, stateFilter, typeFilter)
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, [rid]: false }))
    }
  }

  const handleMarkSuccess = async (rid) => {
    setActionBusy((b) => ({ ...b, [rid]: true }))
    try {
      await api.post(`/api/runs/${rid}/mark-success`)
      message.success('实例已强制成功')
      fetchRuns(selectedWid, stateFilter, typeFilter)
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, [rid]: false }))
    }
  }

  const handleBatchStop = async () => {
    setBatchStopping(true)
    const ids = selectedRowKeys
    let ok = 0, fail = 0
    for (const rid of ids) {
      try {
        await api.post(`/api/runs/${rid}/stop`)
        ok++
      } catch {
        fail++
      }
    }
    message.success(`已终止 ${ok} 个实例${fail ? `, ${fail} 个失败` : ''}`)
    setSelectedRowKeys([])
    setBatchStopping(false)
    fetchRuns(selectedWid, stateFilter, typeFilter)
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 60,
    },
    {
      title: '工作流',
      dataIndex: 'workflow_name',
      width: 160,
      render: (name, row) => (
        <Link to={`/workflows/${row.workflow_id}`}>{name ?? row.workflow_id ?? '—'}</Link>
      ),
    },
    {
      title: '类型',
      dataIndex: 'run_type',
      width: 90,
      render: (v) => (
        <Tag color={RUN_TYPE_COLOR[v] ?? 'default'}>{v ?? '—'}</Tag>
      ),
    },
    {
      title: '数据区间',
      key: 'interval',
      render: (_, row) => (
        <span style={{ fontSize: 12 }}>
          {fmt(row.data_interval_start)} ~ {fmt(row.data_interval_end)}
        </span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 100,
      render: (v) => <StateTag state={v} />,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 160,
      render: fmt,
    },
    {
      title: '完成时间',
      dataIndex: 'finished_at',
      width: 160,
      render: fmt,
    },
    {
      title: '操作',
      width: 230,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/runs/${row.id}`)}
          >
            详情
          </Button>
          {row.state === 'running' && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              loading={actionBusy[row.id]}
              onClick={() => handleStop(row.id)}
            >
              终止
            </Button>
          )}
          {(row.state === 'failed' || row.state === 'stopped') && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={actionBusy[row.id]}
              onClick={() => handleRetry(row.id)}
            >
              重跑
            </Button>
          )}
          {(row.state === 'failed' || row.state === 'stopped') && (
            <Popconfirm
              title="将该实例所有未成功任务置为成功?"
              onConfirm={() => handleMarkSuccess(row.id)}
              okText="确认"
              cancelText="取消"
            >
              <Button
                size="small"
                icon={<CheckCircleOutlined />}
                loading={actionBusy[row.id]}
              >
                强制成功
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>实例监控</Typography.Title>
          <Typography.Text type="secondary">查看全部运行实例,每 5 秒自动刷新</Typography.Text>
        </div>
      </div>

      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          style={{ width: 220 }}
          placeholder="全部工作流"
          allowClear
          loading={loadingWf}
          value={selectedWid}
          onChange={(v) => {
            const wid = v ?? null
            setSelectedWid(wid)
            syncFilter(wid, stateFilter, typeFilter)
            setRuns([])
          }}
          options={workflows.map((w) => ({ label: w.name, value: w.id }))}
          showSearch
          optionFilterProp="label"
        />
        <Select
          style={{ width: 130 }}
          placeholder="状态筛选"
          allowClear
          value={stateFilter}
          onChange={(v) => {
            const s = v ?? null
            setStateFilter(s)
            syncFilter(selectedWid, s, typeFilter)
          }}
          options={[
            { label: '运行中', value: 'running' },
            { label: '成功', value: 'success' },
            { label: '失败', value: 'failed' },
            { label: '已停止', value: 'stopped' },
            { label: '等待', value: 'queued' },
          ]}
        />
        <Select
          style={{ width: 130 }}
          placeholder="类型筛选"
          allowClear
          value={typeFilter}
          onChange={(v) => {
            const t = v ?? null
            setTypeFilter(t)
            syncFilter(selectedWid, stateFilter, t)
          }}
          options={[
            { label: '手动', value: 'manual' },
            { label: '调度', value: 'scheduled' },
            { label: '补数', value: 'backfill' },
          ]}
        />
      </Space>

      {selectedRowKeys.length > 0 && (
        <div style={{ marginBottom: 12, padding: '8px 12px', background: '#fff7e6', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Typography.Text strong>已选 {selectedRowKeys.length} 个实例</Typography.Text>
          <Popconfirm
            title={`确认终止选中的 ${selectedRowKeys.length} 个运行中实例？`}
            onConfirm={handleBatchStop}
            okText="确认终止"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<StopOutlined />} loading={batchStopping}>
              批量终止
            </Button>
          </Popconfirm>
          <Button size="small" onClick={() => setSelectedRowKeys([])}>取消选择</Button>
        </div>
      )}

      <Table
        rowKey="id"
        loading={loadingRuns}
        dataSource={runs}
        columns={columns}
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
          getCheckboxProps: (r) => ({ disabled: r.state !== 'running' }),
        }}
        pagination={{
          current: undefined,
          pageSize,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          hideOnSinglePage: true,
          onShowSizeChange: (_current, size) => setPageSize(size),
        }}
        size="small"
        locale={{ emptyText: '暂无实例' }}
      />
    </div>
  )
}
