import { Typography } from 'antd'
import { useParams } from 'react-router-dom'

export default function RunDetail() {
  const { id } = useParams()
  return (
    <div>
      <Typography.Title level={4}>实例详情 #{id}</Typography.Title>
      <Typography.Text type="secondary">任务实例/日志 — 即将实现</Typography.Text>
    </div>
  )
}
