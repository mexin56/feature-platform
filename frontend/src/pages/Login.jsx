import { Button, Card, Form, Input, Typography, message } from 'antd'
import { useState } from 'react'

import { api } from '../api.js'

export default function Login({ onLogin }) {
  const [busy, setBusy] = useState(false)

  const submit = async (values) => {
    setBusy(true)
    try {
      const r = await api.post('/api/auth/login', values)
      localStorage.setItem('token', r.token)
      message.success(`欢迎,${r.user?.username ?? values.username}`)
      if (onLogin) onLogin()
    } catch (e) {
      // error already shown by api.js
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f0f2f5',
      }}
    >
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ textAlign: 'center' }}>
          特征调度管理平台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          Feature Platform — 请登录
        </Typography.Paragraph>
        <Form layout="vertical" onFinish={submit}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, min: 6, message: '至少 6 位' }]}
          >
            <Input.Password onPressEnter={(e) => e.target.form.requestSubmit?.()} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
