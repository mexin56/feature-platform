import { PlayCircleOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Input, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'

import { api } from '../api.js'

export default function Query() {
  const [connections, setConnections] = useState([])
  const [views, setViews] = useState([])
  const [engine, setEngine] = useState('duckdb') // 'duckdb' 或连接 id(number)
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    api.get('/api/connections').then((r) => setConnections(r.items ?? r)).catch(() => {})
    api.get('/api/feature-groups')
      .then((fgs) => setViews((fgs.items ?? fgs)
        .filter((f) => f.offline_kind === 'parquet')
        .map((f) => f.name)))
      .catch(() => {})
  }, [])

  const run = async () => {
    if (!sql.trim()) {
      message.warning('请输入 SQL')
      return
    }
    setBusy(true)
    setResult(null)
    try {
      const body = engine === 'duckdb'
        ? { engine: 'duckdb', sql, limit: 500 }
        : { engine: 'connection', connection_id: engine, sql, limit: 500 }
      const r = await api.post('/api/query', body)
      setResult(r)
    } catch (e) {
      // 错误已由 api.js 弹出
    } finally {
      setBusy(false)
    }
  }

  const columns = (result?.columns ?? []).map((c, i) => ({
    title: c,
    dataIndex: String(i),
    key: `${c}_${i}`,
    ellipsis: true,
  }))
  const data = (result?.rows ?? []).map((r, idx) => ({
    key: idx,
    ...Object.fromEntries(r.map((v, i) => [String(i), v === null ? '∅' : String(v)])),
  }))

  return (
    <div>
      <Typography.Title level={4}>数据查询</Typography.Title>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space wrap>
            <span>查询引擎:</span>
            <Select
              style={{ width: 280 }}
              value={engine}
              onChange={setEngine}
              options={[
                { value: 'duckdb', label: '本地 DuckDB(特征快照)' },
                ...connections.map((c) => ({
                  value: c.id,
                  label: `${c.name}(${c.conn_type})`,
                })),
              ]}
            />
            {engine === 'duckdb' && views.length > 0 && (
              <span>
                可用视图:
                {views.map((v) => (
                  <Tag
                    key={v}
                    color="blue"
                    style={{ cursor: 'pointer' }}
                    onClick={() => setSql(`select * from "${v}" limit 100`)}
                  >
                    {v}
                  </Tag>
                ))}
              </span>
            )}
          </Space>
          <Input.TextArea
            rows={5}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            placeholder={engine === 'duckdb'
              ? '仅支持只读查询;特征组 Parquet 快照已注册为同名视图,也可直接 read_parquet(...);支持 Ctrl+Enter 运行'
              : '仅支持只读查询,SELECT 会自动包一层行数限制下推执行;支持 Ctrl+Enter 运行'}
            onKeyDown={(e) => {
              if (e.ctrlKey && e.key === 'Enter') run()
            }}
            style={{ fontFamily: 'Consolas, Monaco, monospace' }}
          />
          <Space>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={busy} onClick={run}>
              运行
            </Button>
            {result && (
              <Typography.Text type="secondary">
                {result.row_count} 行{result.truncated ? '(已截断,上限 500)' : ''} ·{' '}
                {result.elapsed_ms} ms
              </Typography.Text>
            )}
          </Space>
        </Space>
      </Card>
      <Card size="small">
        {result ? (
          <Table
            size="small"
            columns={columns}
            dataSource={data}
            scroll={{ x: 'max-content' }}
            pagination={{ pageSize: 50, showSizeChanger: false }}
          />
        ) : (
          <Empty description="运行查询后在此查看结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>
    </div>
  )
}
