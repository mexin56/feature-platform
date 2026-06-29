import {
  ReloadOutlined,
  StopOutlined,
  FileTextOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons'
import {
  Button,
  Descriptions,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api, authHeaders } from '../api.js'
import StateTag from '../components/StateTag.jsx'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

const TERMINAL_STATES = new Set(['success', 'failed', 'stopped'])

function duration(started, finished) {
  if (!started) return '—'
  const end = finished ? new Date(finished) : new Date()
  const s = Math.round((end - new Date(started)) / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m >= 60) {
    const h = Math.floor(m / 60)
    return `${h}h ${m % 60}m ${rem}s`
  }
  return `${m}m ${rem}s`
}

const RUN_TYPE_COLOR = {
  manual: 'blue',
  scheduled: 'green',
  backfill: 'gold',
}

export default function RunDetail() {
  const { id } = useParams()
  const rid = parseInt(id, 10)

  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionBusy, setActionBusy] = useState({})

  // Log modal state
  const [logModal, setLogModal] = useState(false)
  const [logTask, setLogTask] = useState(null) // task object
  const [logContent, setLogContent] = useState('')
  const [logLoading, setLogLoading] = useState(false)
  const logEndRef = useRef(null)

  const intervalRef = useRef(null)

  const fetchDetail = () => {
    api.get(`/api/runs/${rid}`)
      .then(setRun)
      .catch(() => {})
  }

  useEffect(() => {
    setLoading(true)
    api.get(`/api/runs/${rid}`)
      .then((data) => { setRun(data) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [rid])

  // 5s polling until terminal state
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current)
    if (!run) return
    if (TERMINAL_STATES.has(run.state)) return // stop polling once done

    intervalRef.current = setInterval(() => {
      if (document.hidden) return
      fetchDetail()
    }, 5000)
    return () => clearInterval(intervalRef.current)
  }, [run?.state])

  const handleStop = async () => {
    setActionBusy((b) => ({ ...b, stop: true }))
    try {
      await api.post(`/api/runs/${rid}/stop`)
      message.success('实例已终止')
      fetchDetail()
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, stop: false }))
    }
  }

  const handleRetry = async () => {
    setActionBusy((b) => ({ ...b, retry: true }))
    try {
      await api.post(`/api/runs/${rid}/retry`)
      message.success('实例已重跑')
      fetchDetail()
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, retry: false }))
    }
  }

  const handleMarkSuccess = async (tid) => {
    setActionBusy((b) => ({ ...b, [`ms_${tid}`]: true }))
    try {
      await api.post(`/api/tasks/${tid}/mark-success`)
      message.success('任务已置成功')
      fetchDetail()
    } catch {
      // shown by api.js
    } finally {
      setActionBusy((b) => ({ ...b, [`ms_${tid}`]: false }))
    }
  }

  const openLog = async (task) => {
    setLogTask(task)
    setLogContent('')
    setLogModal(true)
    setLogLoading(true)
    try {
      const resp = await fetch(`/api/tasks/${task.id}/log`, { headers: authHeaders() })
      if (!resp.ok) {
        const text = await resp.text()
        setLogContent(`[错误] ${text || `HTTP ${resp.status}`}`)
      } else {
        const text = await resp.text()
        setLogContent(text)
      }
    } catch (e) {
      setLogContent(`[加载失败] ${e.message}`)
    } finally {
      setLogLoading(false)
    }
  }

  // Scroll log to bottom when content loads
  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logContent])

  const taskColumns = [
    {
      title: '任务 Key',
      dataIndex: 'task_key',
      render: (v, row) => (
        <span>
          <code style={{ fontSize: 12 }}>{v}</code>
          {row.task_type && (
            <Tag style={{ marginLeft: 6, fontSize: 11 }}>{row.task_type}</Tag>
          )}
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
      title: '尝试',
      key: 'tries',
      width: 80,
      render: (_, row) => `${row.try_number ?? 0} / ${row.max_tries ?? 1}`,
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      width: 155,
      render: fmt,
    },
    {
      title: '完成时间',
      dataIndex: 'finished_at',
      width: 155,
      render: fmt,
    },
    {
      title: '耗时',
      key: 'duration',
      width: 80,
      render: (_, row) => duration(row.started_at, row.finished_at),
    },
    {
      title: '结果',
      dataIndex: 'result_json',
      width: 80,
      render: (v) => {
        if (!v) return '—'
        let display
        try {
          const parsed = JSON.parse(v)
          display = JSON.stringify(parsed, null, 2)
        } catch {
          display = v
        }
        const short = display.length > 30 ? display.slice(0, 30) + '…' : display
        return (
          <Tooltip title={<pre style={{ maxWidth: 400, maxHeight: 300, overflow: 'auto', margin: 0, fontSize: 11 }}>{display}</pre>}>
            <code style={{ fontSize: 11, cursor: 'help' }}>{short}</code>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      width: 150,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<FileTextOutlined />}
            onClick={() => openLog(row)}
          >
            日志
          </Button>
          {row.state !== 'running' && (
            <Button
              size="small"
              icon={<CheckCircleOutlined />}
              loading={actionBusy[`ms_${row.id}`]}
              onClick={() => handleMarkSuccess(row.id)}
            >
              置成功
            </Button>
          )}
        </Space>
      ),
    },
  ]

  if (loading) {
    return (
      <div style={{ textAlign: 'center', paddingTop: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!run) {
    return (
      <div>
        <Typography.Text type="danger">实例不存在或无权访问</Typography.Text>
      </div>
    )
  }

  const isRunning = run.state === 'running'
  const canRetry = run.state === 'failed' || run.state === 'stopped'

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            实例详情 #{run.id}
          </Typography.Title>
          <Typography.Text type="secondary">
            工作流 #{run.workflow_id}
          </Typography.Text>
        </div>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            disabled={!canRetry}
            loading={actionBusy.retry}
            onClick={handleRetry}
          >
            重跑
          </Button>
          <Button
            danger
            icon={<StopOutlined />}
            disabled={!isRunning}
            loading={actionBusy.stop}
            onClick={handleStop}
          >
            终止
          </Button>
        </Space>
      </div>

      {/* Run metadata */}
      <Descriptions
        bordered
        size="small"
        column={3}
        style={{ marginBottom: 24 }}
      >
        <Descriptions.Item label="实例 ID">{run.id}</Descriptions.Item>
        <Descriptions.Item label="工作流 ID">{run.workflow_id}</Descriptions.Item>
        <Descriptions.Item label="类型">
          <Tag color={RUN_TYPE_COLOR[run.run_type] ?? 'default'}>{run.run_type}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <StateTag state={run.state} />
        </Descriptions.Item>
        <Descriptions.Item label="数据区间起点">{fmt(run.data_interval_start)}</Descriptions.Item>
        <Descriptions.Item label="数据区间终点">{fmt(run.data_interval_end)}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{fmt(run.created_at)}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{fmt(run.finished_at)}</Descriptions.Item>
        <Descriptions.Item label="总耗时">
          {run.state === 'running'
            ? `运行中 · ${duration(run.created_at, run.finished_at)}`
            : duration(run.created_at, run.finished_at)}
        </Descriptions.Item>
      </Descriptions>

      {/* Task instances table */}
      <Typography.Title level={5} style={{ marginBottom: 8 }}>任务实例</Typography.Title>
      <Table
        rowKey="id"
        dataSource={run.tasks ?? []}
        columns={taskColumns}
        pagination={false}
        size="small"
        locale={{ emptyText: '暂无任务实例' }}
      />

      {/* Log Modal */}
      <Modal
        title={
          logTask
            ? `日志 — ${logTask.task_key} (try ${logTask.try_number ?? 0})`
            : '日志'
        }
        open={logModal}
        onCancel={() => setLogModal(false)}
        footer={
          <Button onClick={() => setLogModal(false)}>关闭</Button>
        }
        width={860}
        destroyOnClose
      >
        {logLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : (
          <pre
            style={{
              background: '#141414',
              color: '#d4d4d4',
              padding: 16,
              borderRadius: 6,
              maxHeight: 480,
              overflowY: 'auto',
              fontSize: 12,
              lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}
          >
            {logContent || '（日志为空）'}
            <span ref={logEndRef} />
          </pre>
        )}
      </Modal>
    </div>
  )
}
