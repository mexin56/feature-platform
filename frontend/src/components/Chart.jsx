import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'

export default function Chart({ option, height = 320 }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chart.setOption(option)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [JSON.stringify(option)])
  return <div ref={ref} style={{ height, width: '100%' }} />
}
