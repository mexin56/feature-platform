import {
  EyeOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons'
import {
  Button,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

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
  const [actionBusy, setActionBusy] = useState({}) // rid → bool
  const navigate = useNavigate()
  const intervalRef = useRef(null)

  // Load workflow list once on mount
  useEffect(() => {
    setLoadingWf(true)
    api.get('/api/workflows')
      .then((list) => {
        setWorkflows(list)
        if (list.length > 0) setSelectedWid(list[0].id)
      })
      .catch(() => {})
      .finally(() => setLoadingWf(false))
  }, [])

  const fetchRuns = (wid) => {
    if (!wid) return
    api.get(`/api/workflows/${wid}/runs`)
      .then(setRuns)
      .catch(() => {})
  }

  // Fetch runs when selected workflow changes
  useEffect(() => {
    if (!selectedWid) return
    setLoadingRuns(true)
    api.get(`/api/workflows/${selectedWid}/runs`)
      .then(setRuns)
      .catch(() => {})
      .finally(() => setLoadingRuns(false))
  }, [selectedWid])

  // 5s polling — skip when tab hidden
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (!selectedWid) return
    intervalRef.current = setInterval(() => {
      if (document.hidden) return
      fetchRuns(selectedWid)
    }, 5000)
    return () => clearInterval(intervalRef.current)
  }, [selectedWid])

  const handleStop = async (rid) => {
    setActionBusy((b) => ({ ...b, [rid]: true }))
    try {
      await api.post(`/api/runs/${rid}/stop`)
      message.success('实例已终止')
      fetchRuns(selectedWid)
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
      fetchRuns(selectedWid)
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, [rid]: false }))
    }
  }

  // Filter runs client-side
  const filtered = runs.filter((r) => {
    if (stateFilter && r.state !== stateFilter) return false
    if (typeFilter && r.run_type !== typeFilter) return false
    return true
  })

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 60,
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
      width: 170,
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
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>实例监控</Typography.Title>
          <Typography.Text type="secondary">按工作流查看运行实例,每 5 秒自动刷新</Typography.Text>
        </div>
      </div>

      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          style={{ width: 220 }}
          placeholder="选择工作流"
          loading={loadingWf}
          value={selectedWid}
          onChange={(v) => {
            setSelectedWid(v)
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
          onChange={setStateFilter}
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
          onChange={setTypeFilter}
          options={[
            { label: '手动', value: 'manual' },
            { label: '调度', value: 'scheduled' },
            { label: '补数', value: 'backfill' },
          ]}
        />
      </Space>

      <Table
        rowKey="id"
        loading={loadingRuns}
        dataSource={filtered}
        columns={columns}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        size="small"
        locale={{ emptyText: selectedWid ? '暂无实例' : '请先选择工作流' }}
      />
    </div>
  )
}
