import type { AuthUser, RunDetail, RunSummary } from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail ?? '请求失败')
  }
  return response.json() as Promise<T>
}

export const api = {
  login: (username: string, password: string) =>
    request<AuthUser>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<AuthUser>('/api/auth/me'),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),
  listRuns: () => request<RunSummary[]>('/api/runs'),
  getRun: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  createRun: (query: string, lineId = 'LINE-02') =>
    request<RunSummary>('/api/runs', {
      method: 'POST',
      body: JSON.stringify({ query, line_id: lineId }),
    }),
  approve: (id: string, approved: boolean, approver: string, comment: string) =>
    request<RunSummary>(`/api/runs/${id}/approval`, {
      method: 'POST',
      body: JSON.stringify({ approved, approver, comment }),
    }),
  eventUrl: (id: string) => `${API_BASE}/api/runs/${id}/events`,
}
