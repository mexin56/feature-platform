import {
  PlusOutlined,
  PlayCircleOutlined,
  EditOutlined,
  RollbackOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import {
  Button,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api.js'

const fmt = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—')

export default function Workflows() {
  const [workflows, setWorkflows] = useState([])
  const [loading, setLoading] = useState(false)

  // Trigger modal
  const [triggerModal, setTriggerModal] = useState(null) // workflow object
  const [triggerBusy, setTriggerBusy] = useState(false)
  const [triggerForm] = Form.useForm()

  // Backfill modal
  const [backfillModal, setBackfillModal] = useState(null) // workflow object
  const [backfillBusy, setBackfillBusy] = useState(false)
  const [backfillForm] = Form.useForm()

  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    api.get('/api/workflows')
      .then(setWorkflows)
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleStatusToggle = async (wf, checked) => {
    try {
      if (checked) {
        await api.post(`/api/workflows/${wf.id}/online`)
        message.success(`${wf.name} 已上线`)
      } else {
        await api.post(`/api/workflows/${wf.id}/offline`)
        message.success(`${wf.name} 已下线`)
      }
      load()
    } catch {
      // error shown by api.js
    }
  }

  const handleTrigger = async (values) => {
    if (!triggerModal) return
    setTriggerBusy(true)
    try {
      const payload = {}
      if (values.data_interval_start) {
        payload.data_interval_start = values.data_interval_start.toISOString()
      }
      if (values.data_interval_end) {
        payload.data_interval_end = values.data_interval_end.toISOString()
      }
      await api.post(`/api/workflows/${triggerModal.id}/trigger`, payload)
      message.success('触发成功,实例已创建')
      setTriggerModal(null)
      triggerForm.resetFields()
    } catch {
      // shown by api.js
    } finally {
      setTriggerBusy(false)
    }
  }

  const handleBackfill = async (values) => {
    if (!backfillModal) return
    setBackfillBusy(true)
    try {
      const [start, end] = values.range
      const result = await api.post(`/api/workflows/${backfillModal.id}/backfill`, {
        start_date: start.toISOString(),
        end_date: end.toISOString(),
        parallel: values.parallel ?? 1,
      })
      message.success(`补数完成,创建了 ${result.created} 个实例${result.skipped > 0 ? `, ${result.skipped} 个已存在跳过` : ''}`)
      setBackfillModal(null)
      backfillForm.resetFields()
    } catch {
      // shown by api.js
    } finally {
      setBackfillBusy(false)
    }
  }

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (v, row) => (
        <a onClick={() => navigate(`/workflows/${row.id}`)}>{v}</a>
      ),
    },
    {
      title: 'Cron',
      dataIndex: 'cron',
      render: (v) => v ? <code style={{ fontSize: 12 }}>{v}</code> : <Tag>无</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v, row) => (
        <Switch
          size="small"
          checked={v === 'online'}
          checkedChildren="上线"
          unCheckedChildren="下线"
          onChange={(checked) => handleStatusToggle(row, checked)}
        />
      ),
    },
    {
      title: '当前版本',
      dataIndex: 'version_no',
      width: 90,
      render: (v) => (v != null ? `v${v}` : '—'),
    },
    {
      title: '操作',
      width: 220,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => navigate(`/workflows/${row.id}`)}
          >
            编辑
          </Button>
          <Button
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => { setTriggerModal(row); triggerForm.resetFields() }}
          >
            触发
          </Button>
          <Button
            size="small"
            icon={<RollbackOutlined />}
            onClick={() => { setBackfillModal(row); backfillForm.resetFields() }}
          >
            补数
          </Button>
          <Popconfirm
            title={`确认删除工作流"${row.name}"？`}
            description="将同时删除所有运行实例和版本历史，不可恢复"
            onConfirm={async () => {
              try {
                await api.del(`/api/workflows/${row.id}`)
                message.success(`${row.name} 已删除`)
                load()
              } catch { /* shown by api.js */ }
            }}
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>工作流</Typography.Title>
          <Typography.Text type="secondary">管理 DAG 工作流定义、调度与运维操作</Typography.Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/workflows/new')}>
          新建工作流
        </Button>
      </div>

      <Table
        rowKey="id"
        loading={loading}
        dataSource={workflows}
        columns={columns}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: ['10', '20', '50', '100'],
          hideOnSinglePage: true,
        }}
        size="small"
        locale={{ emptyText: '暂无工作流' }}
      />

      {/* 触发 Modal */}
      <Modal
        title={`触发工作流 — ${triggerModal?.name ?? ''}`}
        open={!!triggerModal}
        onCancel={() => { setTriggerModal(null); triggerForm.resetFields() }}
        onOk={() => triggerForm.submit()}
        confirmLoading={triggerBusy}
        destroyOnClose
      >
        <Form form={triggerForm} layout="vertical" onFinish={handleTrigger}>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
            不填区间时,系统自动使用 Cron 推算最近一个数据区间。
          </Typography.Text>
          <Form.Item name="data_interval_start" label="数据区间起点（可选）">
            <DatePicker showTime style={{ width: '100%' }} placeholder="不填则自动推算" />
          </Form.Item>
          <Form.Item name="data_interval_end" label="数据区间终点（可选）">
            <DatePicker showTime style={{ width: '100%' }} placeholder="不填则自动推算" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 补数 Modal */}
      <Modal
        title={`补数 — ${backfillModal?.name ?? ''}`}
        open={!!backfillModal}
        onCancel={() => { setBackfillModal(null); backfillForm.resetFields() }}
        onOk={() => backfillForm.submit()}
        confirmLoading={backfillBusy}
        destroyOnClose
      >
        <Form form={backfillForm} layout="vertical" onFinish={handleBackfill}>
          <Form.Item
            name="range"
            label="补数区间"
            rules={[{ required: true, message: '请选择补数区间' }]}
          >
            <DatePicker.RangePicker showTime style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="parallel"
            label="并发度"
            initialValue={1}
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={20} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
