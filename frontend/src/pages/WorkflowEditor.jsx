import { Typography } from 'antd'
import { useParams } from 'react-router-dom'

export default function WorkflowEditor() {
  const { id } = useParams()
  return (
    <div>
      <Typography.Title level={4}>工作流编辑器 #{id}</Typography.Title>
      <Typography.Text type="secondary">DAG 编辑器 — 即将实现</Typography.Text>
    </div>
  )
}
