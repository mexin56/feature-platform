import {
  AlertOutlined,
  ArrowDownOutlined,
  ArrowUpOutlined,
  FallOutlined,
  RiseOutlined,
  StockOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import {
  Card,
  Col,
  Divider,
  Progress,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import * as echarts from 'echarts/core'
import { BarChart, GaugeChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../api.js'

echarts.use([
  BarChart, CanvasRenderer, GaugeChart, GridComponent,
  LegendComponent, LineChart, TitleComponent, TooltipComponent,
])

export default function MarketSentiment() {
  const [loading, setLoading] = useState(false)
  const [emotion, setEmotion] = useState(null)
  const [fearGreed, setFearGreed] = useState([])
  const [cffexTop, setCffexTop] = useState({})  // { IF: [...], IC: [...], IH: [...], IM: [...] }
  const [cffexTrend, setCffexTrend] = useState([])
  const [lhbList, setLhbList] = useState([])
  const [lhbSummary, setLhbSummary] = useState(null)
  const [marginData, setMarginData] = useState([])

  const fearChartRef = useRef(null)
  const marginChartRef = useRef(null)
  const lhbChartRef = useRef(null)
  const cffexTrendChart = useRef(null)

  const load = async () => {
    setLoading(true)
    try {
      // 每个数据块独立 try/catch,不依赖 catalog 结果
      /* ── 市场情绪 ── */
      ;(async () => {
        try {
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
        } catch {} // eslint-disable-line
      })()

      /* ── 指数行情(恐贪) ── */
      ;(async () => {
        try {
          const idx = await api.post('/api/query', {
            engine: 'duckdb',
            sql: `SELECT trade_date, index_code, index_name, close
  FROM market.ods_akshare_index_daily
  WHERE index_code IN ('sh000016', 'sh000300')
  ORDER BY trade_date`,
            limit: 200,
          })
          if (idx.columns && idx.rows && idx.rows.length) {
            setFearGreed(idx.rows.map(r => Object.fromEntries(idx.columns.map((c, i) => [c, r[i]]))))
          }
        } catch {} // eslint-disable-line
      })()

      /* ── CFFEX 持仓数据 ── */
      ;(async () => {
        try {
          const cf = await api.post('/api/query', {
            engine: 'duckdb',
            sql: `SELECT trade_date, variety, contract, rank,
    long_party_name, long_open_interest, long_open_interest_chg,
    short_party_name, short_open_interest, short_open_interest_chg
  FROM market.ods_akshare_cffex_rank_table
  WHERE trade_date = (SELECT MAX(trade_date) FROM market.ods_akshare_cffex_rank_table)
    AND variety IN ('IF', 'IC', 'IH', 'IM')
  ORDER BY variety, contract, rank`,
            limit: 300,
          })
          if (cf.columns && cf.rows && cf.rows.length) {
            const byVariety = {}
            cf.rows.forEach(r => {
              const row = Object.fromEntries(cf.columns.map((c, i) => [c, r[i]]))
              const v = row.variety
              if (!byVariety[v]) byVariety[v] = []
              byVariety[v].push(row)
            })
            const topByVariety = {}
            for (const [v, rows] of Object.entries(byVariety)) {
              const today = rows[0].trade_date
              const topLong = sumCffexByParty(rows, 'long')
              const topShort = sumCffexByParty(rows, 'short')
              topByVariety[v] = mergeLongShort(topLong, topShort).slice(0, 15).map(r => ({ ...r, trade_date: today }))
            }
            setCffexTop(topByVariety)
          }

          const trend = await api.post('/api/query', {
            engine: 'duckdb',
            sql: `SELECT trade_date,
    SUM(CASE WHEN variety='IF' AND long_party_name LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IF' AND short_party_name LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS citic_if_net_chg,
    SUM(CASE WHEN variety='IF' AND long_party_name NOT LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IF' AND short_party_name NOT LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS other_if_net_chg,
    SUM(CASE WHEN variety='IC' AND long_party_name LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IC' AND short_party_name LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS citic_ic_net_chg,
    SUM(CASE WHEN variety='IC' AND long_party_name NOT LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IC' AND short_party_name NOT LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS other_ic_net_chg,
    SUM(CASE WHEN variety='IH' AND long_party_name LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IH' AND short_party_name LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS citic_ih_net_chg,
    SUM(CASE WHEN variety='IH' AND long_party_name NOT LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IH' AND short_party_name NOT LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS other_ih_net_chg,
    SUM(CASE WHEN variety='IM' AND long_party_name LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IM' AND short_party_name LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS citic_im_net_chg,
    SUM(CASE WHEN variety='IM' AND long_party_name NOT LIKE '%中信%' THEN long_open_interest_chg ELSE 0 END)
      - SUM(CASE WHEN variety='IM' AND short_party_name NOT LIKE '%中信%' THEN short_open_interest_chg ELSE 0 END)
      AS other_im_net_chg
  FROM market.ods_akshare_cffex_rank_table
  WHERE variety IN ('IF', 'IC', 'IH', 'IM') AND rank <= 20
  GROUP BY trade_date
  ORDER BY trade_date`,
            limit: 100,
          })
          if (trend.columns && trend.rows && trend.rows.length) {
            setCffexTrend(trend.rows.map(r => Object.fromEntries(trend.columns.map((c, i) => [c, r[i]]))))
          }
        } catch {} // eslint-disable-line
      })()

      /* ── 龙虎榜 ── */
      ;(async () => {
        try {
          const lhb = await api.post('/api/query', {
            engine: 'duckdb',
            sql: `SELECT trade_date, ts_code, name, close, change_pct,
    billboard_buy_amt, billboard_sell_amt, billboard_net_amt,
    buy_seat, sell_seat, explain
  FROM market.ods_eastmoney_lhb_detail
  WHERE trade_date = (SELECT MAX(trade_date) FROM market.ods_eastmoney_lhb_detail)
  ORDER BY ABS(billboard_net_amt) DESC
  LIMIT 100`,
            limit: 100,
          })
          if (lhb.columns && lhb.rows) {
            const rows = lhb.rows.map(r => Object.fromEntries(lhb.columns.map((c, i) => [c, r[i]])))
            setLhbList(rows)
            let instBuy = 0, instSell = 0, totalNet = 0
            rows.forEach(r => {
              const bCnt = String(r.buy_seat || '').split('3').length - 1
              const sCnt = String(r.sell_seat || '').split('3').length - 1
              instBuy += bCnt; instSell += sCnt
              totalNet += Number(r.billboard_net_amt || 0)
            })
            setLhbSummary({ instBuy, instSell, totalNet: totalNet.toFixed(0), count: rows.length })
          }
        } catch {} // eslint-disable-line
      })()

      /* ── 融资融券 ── */
      ;(async () => {
        try {
          const mg = await api.post('/api/query', {
            engine: 'duckdb',
            sql: `SELECT dim_date, rzye, rzmre, rqye, rzrqye
  FROM market.ods_eastmoney_margin_summary
  ORDER BY dim_date`,
            limit: 30,
          })
          if (mg.columns && mg.rows && mg.rows.length) {
            setMarginData(mg.rows.map(r => Object.fromEntries(mg.columns.map((c, i) => [c, r[i]]))))
          }
        } catch {} // eslint-disable-line
      })()

    } catch (e) {
      console.warn('MarketSentiment load error:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  /* ── 恐惧贪婪指数时间序列（上证50 & 沪深300 RSI 恐贪）── */
  useEffect(() => {
    if (!fearGreed.length || !fearChartRef.current) return
    // 按指数代码分组
    const byCode = {}
    fearGreed.forEach(r => {
      const c = r.index_code
      if (!byCode[c]) byCode[c] = []
      byCode[c].push({ trade_date: r.trade_date, close: Number(r.close) })
    })
    // RSI 转恐贪：仅用近2个月
    const now = new Date()
    const twoMonthsAgo = new Date(now.getFullYear(), now.getMonth() - 2, now.getDate())
    function calcFearGreed(data) {
      const sorted = data.filter(d => new Date(d.trade_date) >= twoMonthsAgo).sort((a, b) => a.trade_date.localeCompare(b.trade_date))
      if (sorted.length < 15) return { dates: sorted.map(d => d.trade_date), values: sorted.map(() => 50) }
      // RSI(14) → 0-100 scale
      const gains = [], losses = []
      for (let i = 1; i < sorted.length; i++) {
        const chg = sorted[i].close - sorted[i-1].close
        gains.push(chg > 0 ? chg : 0)
        losses.push(chg < 0 ? -chg : 0)
      }
      let avgGain = gains.slice(0, 14).reduce((a, b) => a + b, 0) / 14
      let avgLoss = losses.slice(0, 14).reduce((a, b) => a + b, 0) / 14
      const rsi = [100 - (avgLoss === 0 ? 100 : 100 / (1 + avgGain / avgLoss))]
      for (let i = 14; i < gains.length; i++) {
        avgGain = (avgGain * 13 + gains[i]) / 14
        avgLoss = (avgLoss * 13 + losses[i]) / 14
        rsi.push(100 - (avgLoss === 0 ? 100 : 100 / (1 + avgGain / avgLoss)))
      }
      return { dates: sorted.slice(14).map(d => d.trade_date), values: rsi }
    }
    const sh50 = calcFearGreed(byCode['sh000016'] || [])
    const hs300 = calcFearGreed(byCode['sh000300'] || [])
    const dates = sh50.dates
    const chart = echarts.init(fearChartRef.current)
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['上证50恐贪', '沪深300恐贪'], bottom: 0 },
      grid: { left: 60, right: 20, top: 10, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', min: 0, max: 100, name: '恐贪指数' },
      visualMap: {
        min: 0, max: 100, show: false,
        pieces: [
          { min: 0, max: 25, color: '#f5222d' },
          { min: 25, max: 45, color: '#fa8c16' },
          { min: 45, max: 55, color: '#d4b106' },
          { min: 55, max: 75, color: '#52c41a' },
          { min: 75, max: 100, color: '#237804' },
        ],
      },
      series: [
        {
          name: '上证50恐贪', type: 'line', data: sh50.values,
          smooth: true, lineStyle: { width: 2, color: '#1890ff' },
          itemStyle: { color: '#1890ff' }, symbol: 'circle', symbolSize: 4,
          markLine: {
            silent: true,
            data: [
              { yAxis: 25, label: { formatter: '极度恐惧 25', color: '#f5222d', fontSize: 10 }, lineStyle: { type: 'dashed', color: '#f5222d' } },
              { yAxis: 45, label: { formatter: '恐惧 45', color: '#fa8c16', fontSize: 10 }, lineStyle: { type: 'dashed', color: '#fa8c16' } },
              { yAxis: 55, label: { formatter: '中性 55', color: '#d4b106', fontSize: 10 }, lineStyle: { type: 'dashed', color: '#d4b106' } },
              { yAxis: 75, label: { formatter: '贪婪 75', color: '#52c41a', fontSize: 10 }, lineStyle: { type: 'dashed', color: '#52c41a' } },
            ],
          },
        },
        {
          name: '沪深300恐贪', type: 'line', data: hs300.values,
          smooth: true, lineStyle: { width: 2, color: '#f5222d' },
          itemStyle: { color: '#f5222d' }, symbol: 'diamond', symbolSize: 4,
        },
      ],
    }, true)
    return () => chart.dispose()
  }, [fearGreed, loading])

  /* ── margin chart ── */
  useEffect(() => {
    if (!marginData.length || !marginChartRef.current) return
    const chart = echarts.init(marginChartRef.current)
    const dates = marginData.map(r => r.dim_date).reverse()
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['融资余额(亿)', '融资买入(亿)'], bottom: 0 },
      grid: { left: 50, right: 16, top: 10, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: [{ type: 'value', name: '亿' }],
      series: [
        { name: '融资余额(亿)', type: 'line', data: marginData.map(r => (Number(r.rzye) / 1e8).toFixed(1)).reverse(), smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#fa8c16' } },
        { name: '融资买入(亿)', type: 'bar', data: marginData.map(r => (Number(r.rzmre) / 1e8).toFixed(1)).reverse(), barWidth: '40%', itemStyle: { color: '#1890ff' } },
      ],
    }, true)
    return () => chart.dispose()
  }, [marginData, loading])

  /* ── lhb top10 bar ── */
  useEffect(() => {
    if (!lhbList.length || !lhbChartRef.current) return
    const top10 = [...lhbList].sort((a, b) => Math.abs(Number(b.billboard_net_amt) || 0) - Math.abs(Number(a.billboard_net_amt) || 0)).slice(0, 10)
    const chart = echarts.init(lhbChartRef.current)
    chart.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 80, right: 20, top: 10, bottom: 20 },
      xAxis: { type: 'value', name: '净买额' },
      yAxis: { type: 'category', data: top10.map(r => r.name).reverse(), axisLabel: { fontSize: 11 } },
      series: [{ type: 'bar', data: top10.map(r => (Number(r.billboard_net_amt) / 1e4).toFixed(0)).reverse(), itemStyle: { color: (p) => Number(p.value) >= 0 ? '#cf1322' : '#3f8600' } }],
    }, true)
    return () => chart.dispose()
  }, [lhbList, loading])

  /* ── CFFEX 趋势: 各品种 中信 vs 其他 每日净增减 ── */
  useEffect(() => {
    if (!cffexTrend.length || !cffexTrendChart.current) return
    const chart = echarts.init(cffexTrendChart.current)
    const dates = cffexTrend.map(r => r.trade_date)
    const vars = ['IF', 'IC', 'IH', 'IM']
    const citicColors = { IF: '#722ed1', IC: '#eb2f96', IH: '#13c2c2', IM: '#fa8c16' }
    const otherColors = { IF: '#b37feb', IC: '#f759ab', IH: '#5cdbd3', IM: '#ffd591' }
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: vars.flatMap(v => [`中信${v}`, `其他${v}`]), bottom: 0, textStyle: { fontSize: 11 } },
      grid: { left: 60, right: 20, top: 10, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
      yAxis: [{ type: 'value', name: '手', axisLabel: { formatter: '{value} 手' } }],
      series: vars.flatMap(v => [
        {
          name: `中信${v}`, type: 'line', data: cffexTrend.map(r => r[`citic_${v.toLowerCase()}_net_chg`]),
          smooth: true, lineStyle: { width: 2, color: citicColors[v] },
          itemStyle: { color: citicColors[v] }, symbol: 'circle', symbolSize: 4,
        },
        {
          name: `其他${v}`, type: 'line', data: cffexTrend.map(r => r[`other_${v.toLowerCase()}_net_chg`]),
          smooth: true, lineStyle: { width: 2, color: otherColors[v], type: 'dashed' },
          itemStyle: { color: otherColors[v] }, symbol: 'diamond', symbolSize: 4,
        },
      ]),
    }, true)
    return () => chart.dispose()
  }, [cffexTrend, loading])

  /* helpers */
  function sumCffexByParty(rows, side) {
    const map = {}
    const nameKey = side === 'long' ? 'long_party_name' : 'short_party_name'
    const oiKey = side === 'long' ? 'long_open_interest' : 'short_open_interest'
    const chgKey = side === 'long' ? 'long_open_interest_chg' : 'short_open_interest_chg'
    rows.forEach(r => {
      const name = r[nameKey]; if (!name) return
      const oi = Number(r[oiKey] || 0); const chg = Number(r[chgKey] || 0)
      if (!map[name]) map[name] = { party: name, totalOi: 0, totalChg: 0, varieties: new Set() }
      map[name].totalOi += oi; map[name].totalChg += chg; map[name].varieties.add(r.variety)
    })
    return Object.values(map).map(v => ({ ...v, varietyStr: [...v.varieties].join('/'), varieties: undefined })).sort((a, b) => b.totalOi - a.totalOi)
  }
  function mergeLongShort(long, short) {
    const map = {}
    long.forEach(l => { map[l.party] = { ...map[l.party] || { party: l.party, varietyStr: l.varietyStr }, longOi: l.totalOi, longChg: l.totalChg } })
    short.forEach(s => { map[s.party] = { ...map[s.party] || { party: s.party, varietyStr: s.varietyStr }, shortOi: s.totalOi, shortChg: s.totalChg } })
    return Object.values(map).map(v => ({ ...v, longOi: v.longOi || 0, shortOi: v.shortOi || 0, netOi: (v.longOi || 0) - (v.shortOi || 0), longChg: v.longChg || 0, shortChg: v.shortChg || 0 }))
  }

  function calcFearGreedValue(data) {
    if (!data || data.length < 15) return 50
    const sorted = [...data].sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    let gains = 0, losses = 0
    for (let i = sorted.length - 14; i < sorted.length - 1; i++) {
      const chg = sorted[i + 1].close - sorted[i].close
      gains += chg > 0 ? chg : 0
      losses += chg < 0 ? -chg : 0
    }
    const avgGain = gains / 14, avgLoss = losses / 14
    return 100 - (avgLoss === 0 ? 100 : 100 / (1 + avgGain / avgLoss))
  }
  const [fg50, fg300] = useMemo(() => {
    const byCode = {}
    fearGreed.forEach(r => {
      if (!byCode[r.index_code]) byCode[r.index_code] = []
      byCode[r.index_code].push({ trade_date: r.trade_date, close: Number(r.close) })
    })
    return [calcFearGreedValue(byCode['sh000016']), calcFearGreedValue(byCode['sh000300'])]
  }, [fearGreed])
  const fg50Level = useMemo(() => {
    if (!fg50 || isNaN(fg50)) return null
    if (fg50 <= 25) return { label: '极度恐惧', color: '#f5222d' }
    if (fg50 <= 45) return { label: '恐惧', color: '#fa8c16' }
    if (fg50 <= 55) return { label: '中性', color: '#d4b106' }
    if (fg50 <= 75) return { label: '贪婪', color: '#52c41a' }
    return { label: '极度贪婪', color: '#237804' }
  }, [fg50])

  const lhbColumns = [
    { title: '股票', width: 120, render: (_, r) => (<div><Typography.Text code style={{ fontSize: 12 }}>{r.ts_code}</Typography.Text><br /><Typography.Text strong>{r.name}</Typography.Text></div>) },
    { title: '涨跌幅', width: 80, render: (v, r) => { const vv = Number(r.change_pct ?? 0); return <Typography.Text style={{ color: vv >= 0 ? '#cf1322' : '#3f8600' }}>{vv >= 0 ? '+' : ''}{vv}%</Typography.Text> } },
    { title: '机构净买', width: 110, render: (_, r) => { const vv = Number(r.billboard_net_amt || 0); return <Typography.Text strong style={{ color: vv >= 0 ? '#cf1322' : '#3f8600' }}>{(vv / 1e4).toFixed(0)}万</Typography.Text> } },
    { title: '说明', dataIndex: 'explain', ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Typography.Text> },
  ]

  const cffexColumns = [
    { title: '日期', dataIndex: 'trade_date', width: 100, render: (v) => <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text> },
    { title: '机构', dataIndex: 'party', width: 140, render: (v) => <Typography.Text strong={v.includes('中信')}>{v.replace('(合约)', '')}</Typography.Text> },
    { title: '多单', width: 110, render: (_, r) => <Typography.Text style={{ color: '#cf1322' }}>{r.longOi.toLocaleString()}{r.longChg !== 0 && <span style={{ fontSize: 11, marginLeft: 4, color: r.longChg > 0 ? '#cf1322' : '#3f8600' }}>({r.longChg > 0 ? '+' : ''}{r.longChg})</span>}</Typography.Text> },
    { title: '空单', width: 110, render: (_, r) => <Typography.Text style={{ color: '#3f8600' }}>{r.shortOi.toLocaleString()}{r.shortChg !== 0 && <span style={{ fontSize: 11, marginLeft: 4, color: r.shortChg > 0 ? '#cf1322' : '#3f8600' }}>({r.shortChg > 0 ? '+' : ''}{r.shortChg})</span>}</Typography.Text> },
    { title: '净持仓', width: 100, render: (_, r) => { const net = r.netOi; return <Typography.Text strong style={{ color: net > 0 ? '#cf1322' : '#3f8600' }}>{net > 0 ? '+' : ''}{net.toLocaleString()}</Typography.Text> } },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>市场情绪</Typography.Title>
          <Typography.Text type="secondary">机构多空 · 恐慌贪婪 · 龙虎榜 · 融资融券 · 资金流</Typography.Text>
        </div>
      </div>

      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col xs={12} sm={6}>
          <Card size="small" loading={loading} bodyStyle={{ padding: 12 }}>
            <Statistic title="上证指数" value={emotion ? Number(emotion.sh_close).toFixed(2) : '—'} precision={2} prefix={<StockOutlined />} />
            {emotion && <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>涨 {emotion.up_count} / 跌 {emotion.down_count} / 涨停 {emotion.limit_up}</div>}
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" loading={loading} bodyStyle={{ padding: 12 }}>
            <Statistic title="深证成指" value={emotion ? Number(emotion.sz_close).toFixed(2) : '—'} precision={2} prefix={<StockOutlined />} />
            {emotion && <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>成交 {Number(emotion.total_amount_yi || 0).toFixed(0)} 亿</div>}
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" loading={loading} bodyStyle={{ padding: 12 }}>
            <Statistic title="上证50恐贪" value={fg50 ? fg50.toFixed(1) : '—'} suffix="/ 100" prefix={<AlertOutlined style={{ color: fg50Level?.color }} />} valueStyle={{ color: fg50Level?.color }} />
            {fg50Level && <Tag color={fg50Level.color} style={{ marginTop: 4 }}>上证50 {fg50Level.label}</Tag>}
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small" loading={loading} bodyStyle={{ padding: 12 }}>
            <Statistic title="龙虎榜净买额(万)" value={lhbSummary ? (Number(lhbSummary.totalNet) / 1e4).toFixed(0) : '—'}
              prefix={lhbSummary && Number(lhbSummary.totalNet) >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
              valueStyle={{ color: lhbSummary && Number(lhbSummary.totalNet) >= 0 ? '#cf1322' : '#3f8600' }} />
            {lhbSummary && <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>机构买入 {lhbSummary.instBuy} 席 / 卖出 {lhbSummary.instSell} 席</div>}
          </Card>
        </Col>
      </Row>

      {/* ── Charts row ── */}
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col xs={24}>
          <Card size="small" title="上证50 / 沪深300 恐贪指数（近2个月）" loading={loading}>
            <div ref={fearChartRef} style={{ width: '100%', height: 300 }} />
          </Card>
        </Col>
      </Row>

      {/* ── 融资融券 & 龙虎榜 ── */}
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col xs={24} sm={12}>
          <Card size="small" title="融资融券余额趋势" loading={loading}>
            <div ref={marginChartRef} style={{ width: '100%', height: 260 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12}>
          <Card size="small" title="龙虎榜 Top10 净买卖" loading={loading}>
            <div ref={lhbChartRef} style={{ width: '100%', height: 260 }} />
          </Card>
        </Col>
      </Row>

      {/* ── CFFEX 持仓趋势：中信 vs 其他 每日净增减 ── */}
      <Card size="small" title={<span><RiseOutlined style={{ color: '#722ed1' }} /> 股指期货净增减趋势：中信 vs 其他机构</span>} loading={loading} style={{ marginBottom: 12 }}>
        <div ref={cffexTrendChart} style={{ width: '100%', height: 260 }} />
        <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>数据来源: 中金所 CFFEX 每日前20大会员持仓 | 净增减 = 多单变化 − 空单变化（单位：手）</div>
      </Card>

      {/* ── CFFEX 今日排行 ── */}
      <Card size="small" title={<span><RiseOutlined style={{ color: '#722ed1' }} /> 今日股指期货持仓龙虎榜</span>} loading={loading} style={{ marginBottom: 12 }}>
        <Row gutter={[12, 12]}>
          {['IF', 'IC', 'IH', 'IM'].map(v => (
            <Col xs={24} sm={12} key={v}>
              <Typography.Text strong style={{ fontSize: 13, color: '#722ed1', display: 'block', marginBottom: 8 }}>
                {v === 'IF' ? '沪深300' : v === 'IC' ? '中证500' : v === 'IH' ? '上证50' : '中证1000'}（{v}）
              </Typography.Text>
              <Table rowKey="party" size="small" dataSource={cffexTop[v] || []} columns={cffexColumns} pagination={false} locale={{ emptyText: '暂无数据' }} />
            </Col>
          ))}
        </Row>
      </Card>

      {/* ── 龙虎榜明细 ── */}
      <Card size="small" title={<span><ThunderboltOutlined /> 龙虎榜席位明细 ({lhbList.length} 条)</span>} loading={loading} style={{ marginBottom: 12 }}>
        <Table rowKey={(r) => `${r.ts_code}_${r.trade_date}`} size="small" dataSource={lhbList} columns={lhbColumns} pagination={{ pageSize: 15, showSizeChanger: false }} locale={{ emptyText: '暂无龙虎榜数据' }} />
      </Card>
    </div>
  )
}
