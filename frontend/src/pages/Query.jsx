import { DownloadOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  Button, Card, Empty, Input, Select, Space, Spin, Table, Tree, Typography, message,
} from 'antd'
import { useEffect, useState } from 'react'

import { api, authHeaders } from '../api.js'

export default function Query() {
  const [connections, setConnections] = useState([])
  const [engine, setEngine] = useState('duckdb') // 'duckdb' 或连接 id(number)
  const [sql, setSql] = useState('')
  const [busy, setBusy] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [result, setResult] = useState(null)
  // 目录:duckdb → views [{name,columns}];connection → databases [] + tablesByDb {}
  const [views, setViews] = useState([])
  const [databases, setDatabases] = useState([])
  const [tablesByDb, setTablesByDb] = useState({})
  const [catalogBusy, setCatalogBusy] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    api.get('/api/connections').then((r) => setConnections(r.items ?? r)).catch(() => {})
  }, [])

  const loadCatalog = async (eng) => {
    setCatalogBusy(true)
    setViews([])
    setDatabases([])
    setTablesByDb({})
    try {
      if (eng === 'duckdb') {
        const r = await api.get('/api/query/catalog?engine=duckdb')
        setViews(r.views ?? [])
      } else {
        const r = await api.get(`/api/query/catalog?engine=connection&connection_id=${eng}`)
        setDatabases(r.databases ?? [])
      }
    } catch (e) {
      // 错误已由 api.js 弹出
    } finally {
      setCatalogBusy(false)
    }
  }

  useEffect(() => { loadCatalog(engine) }, [engine])

  const loadTables = async (db) => {
    if (tablesByDb[db]) return
    const r = await api.get(
      `/api/query/catalog?engine=connection&connection_id=${engine}&db=${encodeURIComponent(db)}`)
    setTablesByDb((prev) => ({ ...prev, [db]: r.tables ?? [] }))
  }

  const match = (name) => !search || name.toLowerCase().includes(search.toLowerCase())

  const treeData = engine === 'duckdb'
    ? views.filter((v) => match(v.name)).map((v) => ({
        key: `view:${v.name}`,
        title: v.name,
        children: (v.columns ?? []).map((c) => ({
          key: `col:${v.name}.${c.name}`,
          title: <span>{c.name} <Typography.Text type="secondary">{c.dtype}</Typography.Text></span>,
          selectable: false,
          isLeaf: true,
        })),
      }))
    : databases.filter((d) => match(d) || (tablesByDb[d] ?? []).some(match)).map((d) => ({
        key: `db:${d}`,
        title: d,
        isLeaf: false,
        selectable: false,
        children: tablesByDb[d]
          ? tablesByDb[d].filter((t) => match(t) || match(d)).map((t) => ({
              key: `tbl:${d}.${t}`, title: t, isLeaf: true,
            }))
          : undefined,
      }))

  const onTreeSelect = (keys) => {
    const k = keys[0]
    if (!k) return
    if (k.startsWith('view:')) setSql(`select * from "${k.slice(5)}" limit 100`)
    if (k.startsWith('tbl:')) setSql(`select * from ${k.slice(4)} limit 100`)
  }

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

  const exportCsv = async () => {
    if (!sql.trim()) {
      message.warning('请输入 SQL')
      return
    }
    setExporting(true)
    try {
      const body = engine === 'duckdb'
        ? { engine: 'duckdb', sql }
        : { engine: 'connection', connection_id: engine, sql }
      const resp = await fetch('/api/query/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}))
        throw new Error(detail.detail || `导出失败 (${resp.status})`)
      }
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `query_${Date.now()}.csv`
      a.click()
      URL.revokeObjectURL(url)
      message.success('已导出(上限 10 万行,UTF-8 BOM 兼容 Excel)')
    } catch (e) {
      message.error(e.message)
    } finally {
      setExporting(false)
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
      <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
        {/* 左侧:库表目录 */}
        <Card
          size="small"
          style={{ width: 280, flexShrink: 0 }}
          title="库表目录"
          extra={
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => loadCatalog(engine)}
            />
          }
        >
          <Select
            style={{ width: '100%', marginBottom: 8 }}
            value={engine}
            onChange={setEngine}
            options={[
              { value: 'duckdb', label: '本地 DuckDB(特征快照)' },
              ...connections.map((c) => ({ value: c.id, label: `${c.name}(${c.conn_type})` })),
            ]}
          />
          <Input.Search
            placeholder="筛选库/表名"
            allowClear
            size="small"
            style={{ marginBottom: 8 }}
            onChange={(e) => setSearch(e.target.value)}
          />
          {catalogBusy ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
          ) : treeData.length ? (
            <Tree
              blockNode
              treeData={treeData}
              onSelect={onTreeSelect}
              loadData={engine === 'duckdb' ? undefined
                : (node) => loadTables(node.key.slice(3))}
              height={420}
            />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={engine === 'duckdb' ? '本项目暂无 Parquet 特征快照' : '无可见库'}
            />
          )}
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
            点击{engine === 'duckdb' ? '视图' : '表'}名自动生成查询语句
          </Typography.Paragraph>
        </Card>

        {/* 右侧:编辑器 + 结果 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <Card size="small" style={{ marginBottom: 12 }}>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              <Input.TextArea
                rows={5}
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                placeholder="仅支持只读查询(SELECT/WITH/SHOW/DESCRIBE/EXPLAIN);Ctrl+Enter 运行"
                onKeyDown={(e) => { if (e.ctrlKey && e.key === 'Enter') run() }}
                style={{ fontFamily: 'Consolas, Monaco, monospace' }}
              />
              <Space>
                <Button type="primary" icon={<PlayCircleOutlined />} loading={busy} onClick={run}>
                  运行
                </Button>
                <Button icon={<DownloadOutlined />} loading={exporting} onClick={exportCsv}>
                  导出 CSV
                </Button>
                {result && (
                  <Typography.Text type="secondary">
                    {result.row_count} 行{result.truncated ? '(展示截断,导出可取全量)' : ''} ·{' '}
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
      </div>
    </div>
  )
}
