import { Tag } from 'antd'

/**
 * StateTag — maps workflow/task run states to Ant Design Tag colors.
 *
 * States:
 *   success        → green
 *   failed         → red
 *   running        → blue
 *   queued         → gold
 *   stopped        → default
 *   up_for_retry   → orange
 *   upstream_failed→ volcano
 *   skipped        → purple
 *   interrupted    → orange
 *   none / other   → default
 */
const STATE_COLOR = {
  success: 'green',
  failed: 'red',
  running: 'blue',
  queued: 'gold',
  stopped: 'default',
  up_for_retry: 'orange',
  upstream_failed: 'volcano',
  skipped: 'purple',
  interrupted: 'orange',
}

const STATE_LABEL = {
  success: '成功',
  failed: '失败',
  running: '运行中',
  queued: '等待',
  stopped: '已停止',
  up_for_retry: '等待重试',
  upstream_failed: '上游失败',
  skipped: '已跳过',
  interrupted: '已中断',
  none: '无',
}

export default function StateTag({ state }) {
  const color = STATE_COLOR[state] ?? 'default'
  const label = STATE_LABEL[state] ?? state ?? '—'
  return <Tag color={color}>{label}</Tag>
}
