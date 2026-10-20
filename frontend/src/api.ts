const base = import.meta.env.VITE_API_BASE || ''

export function token() { return sessionStorage.getItem('avantia-token') }
export function setToken(value: string | null) {
  if (value) sessionStorage.setItem('avantia-token', value)
  else sessionStorage.removeItem('avantia-token')
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (token()) headers.set('Authorization', `Bearer ${token()}`)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${base}${path}`, { ...options, headers })
  if (!response.ok) {
    let detail = `Error ${response.status}`
    try { detail = (await response.json()).detail || detail } catch { /* use status */ }
    if (response.status === 401 && path !== '/api/login') setToken(null)
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}
