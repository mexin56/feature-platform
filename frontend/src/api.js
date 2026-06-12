import { message } from 'antd'

export function authHeaders() {
  const h = {}
  const token = localStorage.getItem('token')
  const pid = localStorage.getItem('projectId')
  if (token) h.Authorization = `Bearer ${token}`
  if (pid) h['X-Project-Id'] = pid
  return h
}

async function handle(resp) {
  if (resp.status === 401 && localStorage.getItem('token')) {
    localStorage.removeItem('token')
    window.location.href = '/login'
    return
  }
  if (!resp.ok) {
    let detail
    try {
      const body = await resp.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      detail = await resp.text()
    }
    const msg = detail || `HTTP ${resp.status}`
    message.error(msg)
    throw new Error(msg)
  }
  return resp.json()
}

export const api = {
  get: (url) => fetch(url, { headers: authHeaders() }).then(handle),
  post: (url, body) =>
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(handle),
  patch: (url, body) =>
    fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }).then(handle),
  put: (url, body) =>
    fetch(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }).then(handle),
  upload: (url, formData) =>
    fetch(url, { method: 'POST', headers: authHeaders(), body: formData }).then(handle),
  del: (url) => fetch(url, { method: 'DELETE', headers: authHeaders() }).then(handle),
}
