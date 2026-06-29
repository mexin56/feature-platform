import { CheckOutlined } from '@ant-design/icons'
import { Button, Space, Switch, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'

import { api } from '../api.js'

const LEVEL_COLOR = { error: 'red', warning: 'orange', info: 'blue' }
const LEVEL_LABEL = { error: '严重', warning: '警告', info: '提示' }

const KIND_LABEL = {
  run_failed: '运行失败',
  run_success: '运行成功',
  sla_miss: 'SLA 超时',
  materialize_lag: '物化滞后',
  quality_drop: '质量下降',
}

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

export default function Alerts() {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [unreadOnly, setUnreadOnly] = useState(false)

  const load = (uo = unreadOnly) => {
    setLoading(true)
    api.get(`/api/alerts?unread_only=${uo ? 1 : 0}`)
      .then(setAlerts)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const markRead = async (id) => {
    try {
      await api.post(`/api/alerts/${id}/read`)
      message.success('已标记已读')
      load()
    } catch {
      // error shown by api.js
    }
  }

  const columns = [
    {
      title: '级别', dataIndex: 'level', width: 80,
      render: (v) => (
        <Tag color={LEVEL_COLOR[v] ?? 'default'}>{LEVEL_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '类型', dataIndex: 'kind', width: 110,
      render: (v) => KIND_LABEL[v] ?? v,
    },
    { title: '标题', dataIndex: 'title' },
    { title: '详情', dataIndex: 'detail', ellipsis: true },
    {
      title: '时间', dataIndex: 'created_at', width: 160,
      render: fmt,
    },
    {
      title: '操作', key: 'action', width: 100,
      render: (_, r) =>
        r.read ? (
          <Tag color="default">已读</Tag>
        ) : (
          <Button
            size="small"
            icon={<CheckOutlined />}
            onClick={() => markRead(r.id)}
          >
            标记已读
          </Button>
        ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>告警中心</Typography.Title>
          <Typography.Text type="secondary">调度失败 / SLA 超时 / 物化滞后 / 质量异常告警</Typography.Text>
        </div>
        <Space>
          <Typography.Text>仅看未读</Typography.Text>
          <Switch
            checked={unreadOnly}
            onChange={(v) => { setUnreadOnly(v); load(v) }}
          />
        </Space>
      </div>

      <Table
        rowKey="id"
        loading={loading}
        dataSource={alerts}
        columns={columns}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          hideOnSinglePage: true,
        }}
        rowClassName={(r) => (r.read ? '' : 'alert-unread')}
        locale={{ emptyText: '暂无告警' }}
      />

      <style>{`
        .alert-unread td { font-weight: 600; }
      `}</style>
    </div>
  )
}
