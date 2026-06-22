import {
  ArrowUpOutlined,
  ArrowDownOutlined,
  DashboardOutlined,
  ThunderboltOutlined,
  StockOutlined,
} from '@ant-design/icons'
import {
  Card,
  Col,
  Descriptions,
  Divider,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'

import { api } from '../api.js'

export default function MarketSentiment() {
  const [loading, setLoading] = useState(false)
  const [emotion, setEmotion] = useState(null)       // market_emotion 当日
  const [lhbList, setLhbList] = useState([])          // 龙虎榜机构
  const [lhbSummary, setLhbSummary] = useState(null)  // 机构汇总

  const load = async () => {
    setLoading(true)
    try {
      // 市场情绪
      const r1 = await api.get('/api/query/catalog?engine=duckdb')
      const marketTables = r1.market_tables ?? []

      // 龙虎榜
      if (marketTables.some(t => t.startsWith('ods_eastmoney_lhb_detail'))) {
        const lhb = await api.post('/api/query', {
          engine: 'duckdb',
          sql: `SELECT trade_date, ts_code, name, close, change_pct, billboard_net_amt, buy_seat, sell_seat, explain
FROM market.ods_eastmoney_lhb_detail
WHERE trade_date = (SELECT MAX(trade_date) FROM market.ods_eastmoney_lhb_detail)
ORDER BY ABS(billboard_net_amt) DESC
LIMIT 100`,
          limit: 100,
        })
        if (lhb.columns && lhb.rows) {
          const rows = lhb.rows.map(r => Object.fromEntries(
            lhb.columns.map((c, i) => [c, r[i]])
          ))
          setLhbList(rows)
          // 汇总
          let instBuy = 0, instSell = 0, totalNet = 0
          rows.forEach(r => {
            const bs = String(r.buy_seat || '')
            const ss = String(r.sell_seat || '')
            instBuy += bs.split('3').length - 1
            instSell += ss.split('3').length - 1
            totalNet += Number(r.billboard_net_amt || 0)
          })
          setLhbSummary({ instBuy, instSell, totalNet: totalNet.toFixed(0), count: rows.length })
        }
      }

      // 市场情绪
      if (marketTables.some(t => t.startsWith('ods_eastmoney_market_emotion'))) {
        const me = await api.post('/api/query', {
          engine: 'duckdb',
          sql: `SELECT * FROM market.ods_eastmoney_market_emotion
WHERE trade_date = (SELECT MAX(trade_date) FROM market.ods_eastmoney_market_emotion)
LIMIT 1`,
          limit: 1,
        })
        if (me.columns && me.rows && me.rows.length) {
          setEmotion(Object.fromEntries(me.columns.map((c, i) => [c, me.rows[0][i]])))
        }
      }
    } catch {
      /* 数据表可能还不存在 */
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const lhbColumns = [
    {
      title: '股票', width: 120,
      render: (_, r) => (
        <div>
          <Typography.Text code style={{ fontSize: 12 }}>{r.ts_code}</Typography.Text>
          <br />
          <Typography.Text strong>{r.name}</Typography.Text>
        </div>
      ),
    },
    {
      title: '涨跌幅', dataIndex: 'change_pct', width: 80,
      render: (v) => (
        <Typography.Text style={{ color: (v ?? 0) >= 0 ? '#cf1322' : '#3f8600' }}>
          {(v ?? 0) >= 0 ? '+' : ''}{v}%
        </Typography.Text>
      ),
    },
    {
      title: '机构买入', width: 90,
      render: (_, r) => {
        const cnt = String(r.buy_seat || '').split('3').length - 1
        return <Tag color={cnt > 0 ? 'green' : 'default'}>{cnt} 席</Tag>
      },
    },
    {
      title: '机构卖出', width: 90,
      render: (_, r) => {
        const cnt = String(r.sell_seat || '').split('3').length - 1
        return <Tag color={cnt > 0 ? 'red' : 'default'}>{cnt} 席</Tag>
      },
    },
    {
      title: '净买额(万)', dataIndex: 'billboard_net_amt', width: 120,
      render: (v) => {
        const val = Number(v || 0)
        return (
          <Typography.Text style={{ color: val >= 0 ? '#cf1322' : '#3f8600' }}>
            {(val / 1e4).toFixed(0)}
          </Typography.Text>
        )
      },
    },
    {
      title: '说明', dataIndex: 'explain', ellipsis: true,
      render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Typography.Text>,
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>市场情绪</Typography.Title>
          <Typography.Text type="secondary">
            机构多空动向 · 龙虎榜席位 · 市场温度
          </Typography.Text>
        </div>
      </div>

      {/* ── 市场情绪卡片 ── */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small" loading={loading}>
            <Statistic
              title="上证指数"
              value={emotion ? Number(emotion.sh_close).toFixed(2) : '—'}
              precision={2}
              prefix={<StockOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading}>
            <Statistic
              title="深证成指"
              value={emotion ? Number(emotion.sz_close).toFixed(2) : '—'}
              precision={2}
              prefix={<StockOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading}>
            <Statistic
              title="机构买入席次(当日)"
              value={lhbSummary?.instBuy ?? '—'}
              prefix={<ArrowUpOutlined />}
              valueStyle={{ color: '#cf1322' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" loading={loading}>
            <Statistic
              title="龙虎榜净买额(万)"
              value={lhbSummary ? (Number(lhbSummary.totalNet) / 1e4).toFixed(0) : '—'}
              prefix={lhbSummary && Number(lhbSummary.totalNet) >= 0
                ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
              valueStyle={{ color: lhbSummary && Number(lhbSummary.totalNet) >= 0 ? '#cf1322' : '#3f8600' }}
            />
          </Card>
        </Col>
      </Row>

      {/* ── 龙虎榜明细 ── */}
      <Card
        size="small"
        title={
          <span><ThunderboltOutlined /> 龙虎榜机构席位 ({lhbList.length} 条)</span>
        }
        loading={loading}
      >
        <Table
          rowKey={(r) => `${r.ts_code}_${r.trade_date}`}
          size="small"
          dataSource={lhbList}
          columns={lhbColumns}
          pagination={{ pageSize: 15, showSizeChanger: false }}
          locale={{ emptyText: '暂无龙虎榜数据,请先采集' }}
        />
      </Card>

      <Divider />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        数据来源: 东方财富龙虎榜明细 (datacenter API) + 腾讯指数行情。
        机构席位编码: 每位数字代表席位性质 (1=营业部, 2=游资, 3=机构专用, 4=其他),
        数据可能存在滞后。
      </Typography.Text>
    </div>
  )
}
