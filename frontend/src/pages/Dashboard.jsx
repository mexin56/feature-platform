import { AlertOutlined, CheckCircleOutlined, CloseCircleOutlined, SyncOutlined } from '@ant-design/icons'
import { Badge, Card, Col, Row, Statistic, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api.js'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

export default function Dashboard() {
  const [dash, setDash] = useState(null)
  const [unread, setUnread] = useState(0)
  const navigate = useNavigate()

  const load = () => {
    api.get('/api/monitoring/dashboard').then(setDash).catch(() => {})
    api.get('/api/alerts?unread_only=1').then((r) => setUnread(r.length)).catch(() => {})
  }

  useEffect(() => { load() }, [])

  const today = dash?.today ?? { success: 0, failed: 0, running: 0 }

  const failureColumns = [
    { title: '运行 ID', dataIndex: 'run_id', width: 80,
      render: (v) => (
        <a onClick={() => navigate(`/runs/${v}`)} style={{ cursor: 'pointer' }}>{v}</a>
      ) },
    { title: '工作流 ID', dataIndex: 'workflow_id', width: 90 },
    { title: '数据区间', dataIndex: 'interval', render: fmt },
    { title: '失败时间', dataIndex: 'finished_at', render: fmt },
  ]

  const fgColumns = [
    { title: '特征组', dataIndex: 'name' },
    { title: '版本', dataIndex: 'version', width: 70 },
    {
      title: '在线', dataIndex: 'online_enabled', width: 70,
      render: (v) => <Tag color={v ? 'green' : 'default'}>{v ? '开启' : '关闭'}</Tag>,
    },
    { title: '最近产出', dataIndex: 'last_produced_at', render: fmt },
    {
      title: '滞后(小时)', dataIndex: 'lag_hours', width: 110,
      render: (v) => {
        if (v == null) return '—'
        return <Tag color={v > 24 ? 'red' : 'green'}>{v}</Tag>
      },
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>工作台</Typography.Title>
          <Typography.Text type="secondary">今日调度大盘 · 特征组物化状态</Typography.Text>
        </div>
        <Badge count={unread} offset={[4, 0]}>
          <Tag
            icon={<AlertOutlined />}
            color={unread ? 'red' : 'default'}
            style={{ cursor: 'pointer', padding: '4px 12px', fontSize: 13 }}
            onClick={() => navigate('/alerts')}
          >
            未读告警
          </Tag>
        </Badge>
      </div>

      {/* 今日调度卡片 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="今日成功"
              value={today.success}
              valueStyle={{ color: '#3f8600' }}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="今日失败"
              value={today.failed}
              valueStyle={{ color: '#cf1322' }}
              prefix={<CloseCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="运行中"
              value={today.running}
              valueStyle={{ color: '#1677ff' }}
              prefix={<SyncOutlined spin={today.running > 0} />}
            />
          </Card>
        </Col>
      </Row>

      {/* 最近失败 */}
      <Card
        title="最近失败实例"
        style={{ marginBottom: 20 }}
        extra={<Typography.Text type="secondary">最近 10 条</Typography.Text>}
      >
        <Table
          rowKey="run_id"
          size="small"
          dataSource={dash?.recent_failures ?? []}
          columns={failureColumns}
          pagination={false}
          locale={{ emptyText: '暂无失败实例' }}
        />
      </Card>

      {/* 特征组物化状态 */}
      <Card
        title="特征组物化状态"
        extra={
          <Typography.Text type="secondary">
            工作流总数：{dash?.workflows_total ?? '—'}
          </Typography.Text>
        }
      >
        <Table
          rowKey="id"
          size="small"
          dataSource={dash?.feature_groups ?? []}
          columns={fgColumns}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{ emptyText: '暂无特征组' }}
        />
      </Card>
    </div>
  )
}
