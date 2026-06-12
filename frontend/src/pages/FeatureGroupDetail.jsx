import { Typography } from 'antd'
import { useParams } from 'react-router-dom'

export default function FeatureGroupDetail() {
  const { id } = useParams()
  return (
    <div>
      <Typography.Title level={4}>特征组详情 #{id}</Typography.Title>
      <Typography.Text type="secondary">特征清单/血缘/在线调试台 — 即将实现</Typography.Text>
    </div>
  )
}
