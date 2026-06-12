import {
  AlertOutlined,
  AppstoreOutlined,
  BranchesOutlined,
  DashboardOutlined,
  KeyOutlined,
  LogoutOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SettingOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons'
import {
  Button,
  Card,
  Dropdown,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { api } from './api.js'
import Admin from './pages/Admin.jsx'
import Alerts from './pages/Alerts.jsx'
import Dashboard from './pages/Dashboard.jsx'
import FeatureGroupDetail from './pages/FeatureGroupDetail.jsx'
import FeatureGroups from './pages/FeatureGroups.jsx'
import Login from './pages/Login.jsx'
import RunDetail from './pages/RunDetail.jsx'
import Runs from './pages/Runs.jsx'
import WorkflowEditor from './pages/WorkflowEditor.jsx'
import Workflows from './pages/Workflows.jsx'

const MENU = [
  { key: '/', icon: <DashboardOutlined />, label: '工作台' },
  { key: '/feature-groups', icon: <AppstoreOutlined />, label: '特征组' },
  { key: '/workflows', icon: <BranchesOutlined />, label: '工作流' },
  { key: '/runs', icon: <PlayCircleOutlined />, label: '实例' },
  { key: '/alerts', icon: <AlertOutlined />, label: '告警' },
]

const ROLE_LABEL = { admin: '管理员', developer: '开发者', viewer: '只读' }

export default function App() {
  const [auth, setAuth] = useState({ state: 'loading' })
  const [projects, setProjects] = useState([])
  const [newProjectModal, setNewProjectModal] = useState(false)
  const [changePassModal, setChangePassModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const [cpForm] = Form.useForm()
  const navigate = useNavigate()
  const location = useLocation()

  const boot = async () => {
    try {
      const token = localStorage.getItem('token')
      if (!token) {
        setAuth({ state: 'login' })
        return
      }
      const me = await api.get('/api/auth/me')
      const list = await api.get('/api/projects').catch(() => [])
      const arr = list.items ?? list
      setProjects(arr)
      // 项目未选或已失效时自动选第一个,避免页面请求缺少 X-Project-Id
      const cur = Number(localStorage.getItem('projectId'))
      if (arr.length && !arr.some((p) => p.id === cur)) {
        localStorage.setItem('projectId', String(arr[0].id))
      }
      if (!arr.length) localStorage.removeItem('projectId')
      setAuth({ state: 'ready', user: me.user ?? me })
    } catch {
      setAuth({ state: 'login' })
    }
  }

  useEffect(() => { boot() }, [])

  if (auth.state === 'loading') {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (auth.state === 'login') {
    return <Login onLogin={() => { boot() }} />
  }
  if (location.pathname === '/login') {
    return <Navigate to="/" replace />  // 已登录访问 /login:回首页(修复重新登录不跳转)
  }

  // 无任何可用项目:引导创建(项目级接口都需要 X-Project-Id,先建项目再进入)
  if (projects.length === 0) {
    const canCreate = auth.user?.role !== 'viewer'
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f7fa' }}>
        <Card title="欢迎使用特征调度管理平台" style={{ width: 420 }}>
          {canCreate ? (
            <>
              <Typography.Paragraph type="secondary">
                还没有可用项目,先创建第一个项目开始使用:
              </Typography.Paragraph>
              <Form
                layout="vertical"
                onFinish={async (values) => {
                  try {
                    const p = await api.post('/api/projects', values)
                    localStorage.setItem('projectId', String(p.id))
                    message.success(`项目 "${p.name}" 已创建`)
                    boot()
                  } catch {
                    // 错误已由 api.js 弹出
                  }
                }}
              >
                <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
                  <Input placeholder="如:反欺诈特征" />
                </Form.Item>
                <Form.Item name="description" label="描述">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Button type="primary" htmlType="submit" block>创建项目</Button>
              </Form>
            </>
          ) : (
            <Typography.Paragraph>
              你还不是任何项目的成员,请联系管理员或项目负责人将你加入项目。
            </Typography.Paragraph>
          )}
          <Button
            type="link"
            style={{ padding: 0, marginTop: 12 }}
            onClick={() => { localStorage.removeItem('token'); localStorage.removeItem('projectId'); window.location.href = '/login' }}
          >
            退出登录
          </Button>
        </Card>
      </div>
    )
  }

  const selected =
    MENU.map((m) => m.key)
      .filter((k) => k !== '/' && location.pathname.startsWith(k))
      .sort((a, b) => b.length - a.length)[0] ||
    (location.pathname.startsWith('/admin') ? '/admin' : '/')

  const isAdmin = auth.user?.role === 'admin'
  const menuItems = isAdmin
    ? [...MENU, { key: '/admin', icon: <SettingOutlined />, label: '管理' }]
    : MENU

  const handleNewProject = async (values) => {
    setBusy(true)
    try {
      const p = await api.post('/api/projects', values)
      message.success(`项目 "${p.name}" 已创建`)
      const updated = await api.get('/api/projects')
      setProjects(updated.items ?? updated)
      localStorage.setItem('projectId', String(p.id))
      setNewProjectModal(false)
      form.resetFields()
    } catch (e) {
      // error already shown by api.js
    } finally {
      setBusy(false)
    }
  }

  const handleChangePassword = async (values) => {
    setBusy(true)
    try {
      await api.post('/api/auth/change-password', {
        old_password: values.old_password,
        new_password: values.new_password,
      })
      message.success('密码已修改')
      setChangePassModal(false)
      cpForm.resetFields()
    } catch (e) {
      // error shown by api.js
    } finally {
      setBusy(false)
    }
  }

  const userMenuItems = [
    { key: 'change-pass', icon: <KeyOutlined />, label: '改密' },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
  ]

  const onUserMenu = ({ key }) => {
    if (key === 'logout') {
      localStorage.removeItem('token')
      localStorage.removeItem('projectId')
      window.location.href = '/login'
    } else if (key === 'change-pass') {
      setChangePassModal(true)
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider
        theme="light"
        width={216}
        style={{ borderRight: '1px solid #eaecf0', position: 'sticky', top: 0, height: '100vh', overflow: 'hidden' }}
      >
        <div className="brand">
          <div className="logo">特</div>
          <div>
            <div className="name">特征调度管理平台</div>
            <div className="sub">Feature Platform</div>
          </div>
        </div>

        {/* 项目切换器 */}
        <div style={{ padding: '0 14px 10px' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Select
              size="small"
              style={{ flex: 1 }}
              value={localStorage.getItem('projectId') ? Number(localStorage.getItem('projectId')) : null}
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
              onChange={(v) => {
                localStorage.setItem('projectId', String(v))
                window.location.reload()
              }}
              placeholder="选择项目"
            />
            <Button
              size="small"
              icon={<PlusOutlined />}
              title="新建项目"
              onClick={() => setNewProjectModal(true)}
            />
          </Space.Compact>
        </div>

        <Menu
          theme="light"
          mode="inline"
          style={{ borderInlineEnd: 'none' }}
          selectedKeys={[selected]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />

        {/* 用户信息 + 下拉 */}
        <div style={{ position: 'absolute', bottom: 14, left: 16, right: 16 }}>
          <Dropdown menu={{ items: userMenuItems, onClick: onUserMenu }} placement="topLeft">
            <Space style={{ cursor: 'pointer', color: '#475467', fontSize: 13 }}>
              <UserOutlined />
              <span>{auth.user?.username}</span>
              <Tag color="geekblue" style={{ margin: 0 }}>
                {ROLE_LABEL[auth.user?.role] || auth.user?.role}
              </Tag>
            </Space>
          </Dropdown>
        </div>
      </Layout.Sider>

      <Layout>
        <Layout.Content style={{ padding: '24px 28px', maxWidth: 1440, width: '100%', margin: '0 auto' }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/feature-groups" element={<FeatureGroups />} />
            <Route path="/feature-groups/:id" element={<FeatureGroupDetail />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/workflows/:id" element={<WorkflowEditor />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/alerts" element={<Alerts />} />
            {isAdmin && <Route path="/admin" element={<Admin />} />}
            <Route path="/login" element={<Navigate to="/" />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </Layout.Content>
      </Layout>

      {/* 新建项目 Modal */}
      <Modal
        title="新建项目"
        open={newProjectModal}
        onCancel={() => { setNewProjectModal(false); form.resetFields() }}
        onOk={() => form.submit()}
        confirmLoading={busy}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={handleNewProject}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input autoFocus placeholder="如: 信贷风控" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 改密 Modal */}
      <Modal
        title="修改密码"
        open={changePassModal}
        onCancel={() => { setChangePassModal(false); cpForm.resetFields() }}
        onOk={() => cpForm.submit()}
        confirmLoading={busy}
        destroyOnClose
      >
        <Form form={cpForm} layout="vertical" onFinish={handleChangePassword}>
          <Form.Item name="old_password" label="当前密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6, message: '至少 6 位' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) return Promise.resolve()
                  return Promise.reject(new Error('两次密码不一致'))
                },
              }),
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}
