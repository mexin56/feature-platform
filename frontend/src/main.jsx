import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App.jsx'
import './styles.css'

const theme = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    borderRadius: 8,
    colorBgLayout: '#f6f7f9',
    colorText: '#1f2329',
    colorTextSecondary: '#667085',
    colorBorder: '#d6dae1',
    colorBorderSecondary: '#eaecf0',
    fontSize: 13,
    fontFamily:
      "-apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif",
  },
  components: {
    Table: {
      headerBg: '#fafbfc',
      headerColor: '#667085',
      headerSplitColor: 'transparent',
      cellPaddingBlock: 13,
      cellPaddingInline: 14,
      rowHoverBg: '#f5f8ff',
    },
    Card: { paddingLG: 20 },
    Layout: { siderBg: '#eef1f5', bodyBg: '#f6f7f9' },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#475467',
      itemSelectedBg: '#eef4ff',
      itemSelectedColor: '#2563eb',
      itemBorderRadius: 8,
      itemMarginInline: 10,
      itemHeight: 38,
    },
    Tabs: { titleFontSize: 13, horizontalItemGutter: 24 },
    Button: { fontWeight: 500 },
    Modal: { titleFontSize: 15 },
    Drawer: { paddingLG: 20 },
  },
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
)
