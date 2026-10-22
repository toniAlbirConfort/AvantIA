import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api, setToken, token } from './api'
import type { Conversation, ImportBatch, Property, Summary, User } from './types'

type View = 'summary' | 'inbox' | 'bookings' | 'properties' | 'imports' | 'integrations' | 'users'
const navigation: { id: View; label: string; code: string }[] = [
  { id: 'summary', label: 'Resumen', code: '01' },
  { id: 'inbox', label: 'Bandeja', code: '02' },
  { id: 'bookings', label: 'Prerreservas', code: '03' },
  { id: 'properties', label: 'Alojamientos', code: '04' },
  { id: 'imports', label: 'Importaciones', code: '05' },
  { id: 'integrations', label: 'Integraciones', code: '06' },
  { id: 'users', label: 'Usuarios', code: '07' },
]
const fieldLabels: Record<string, string> = {
  property_id: 'ID alojamiento', property_name: 'Nombre', capacity: 'Capacidad',
  start_date: 'Entrada / inicio', end_date: 'Salida / fin', status: 'Estado',
}

function date(value?: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}
function label(value: string) {
  return ({ pending: 'Pendiente', queued: 'En cola', sending: 'Enviando', sent: 'Enviado',
    delivery_unknown: 'Envío incierto', closed: 'Cerrado', available: 'Disponible',
    unavailable: 'No disponible', manual_check: 'Revisión manual',
    ambiguous_property: 'Alojamiento ambiguo', valid: 'Válido', invalid: 'Inválido',
    whatsapp: 'WhatsApp', email: 'Email', booking: 'Booking' } as Record<string, string>)[value] || value
}
function Badge({ value }: { value: string }) { return <span className={`badge badge-${value}`}>{label(value)}</span> }
function Empty({ children }: { children: React.ReactNode }) { return <div className="empty">{children}</div> }

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await api<{ token: string; user: User }>('/api/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      })
      setToken(result.token); onLogin(result.user)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  return <div className="login-page"><div className="login-card">
    <div className="brand-mark">A<span>·</span></div><p className="eyebrow">AVANTIA / ACCESO</p>
    <h1>Atención que<br />siempre llega.</h1>
    <p className="muted">Bandeja de consultas y revisión de respuestas para el equipo de alojamientos.</p>
    <form onSubmit={submit} className="stack">
      <label>Usuario<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label>
      <label>Contraseña<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Accediendo…' : 'Entrar al panel →'}</button>
    </form>
  </div><div className="login-aside"><p>01 / RECIBIR</p><p>02 / COMPROBAR</p><p>03 / REVISAR</p><p>04 / RESPONDER</p></div></div>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(!!token())
  const path = window.location.pathname
  const [view, setView] = useState<View>(path.startsWith('/inbox') ? 'inbox' : 'summary')
  const [selectedId, setSelectedId] = useState<number | null>(Number(path.split('/')[2]) || null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [items, setItems] = useState<Conversation[]>([])
  const [detail, setDetail] = useState<Conversation | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [properties, setProperties] = useState<Property[]>([])
  const [imports, setImports] = useState<ImportBatch[]>([])
  const [integrations, setIntegrations] = useState<Record<string, any> | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [draftText, setDraftText] = useState('')
  const [draftDirty, setDraftDirty] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')
  const [mobileMenu, setMobileMenu] = useState(false)

  useEffect(() => {
    if (!token()) return
    api<User>('/api/me').then(setUser).catch(() => setToken(null)).finally(() => setChecking(false))
  }, [])

  const refresh = useCallback(async () => {
    if (!user) return
    try {
      const [nextSummary, nextItems, nextUsers] = await Promise.all([
        api<Summary>('/api/summary'), api<Conversation[]>('/api/conversations'), api<User[]>('/api/users'),
      ])
      const nextIntegrations = await api<Record<string, any>>('/api/integrations')
      setSummary(nextSummary); setItems(nextItems); setUsers(nextUsers)
      setIntegrations(nextIntegrations)
      if (view === 'properties') setProperties(await api<Property[]>('/api/properties'))
      if (view === 'imports') setImports(await api<ImportBatch[]>('/api/imports'))
      if (selectedId) {
        const nextDetail = await api<Conversation>(`/api/conversations/${selectedId}`)
        setDetail(nextDetail)
      }
      setError('')
    } catch (err) { setError((err as Error).message) }
  }, [user, view, selectedId])

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 15000); return () => clearInterval(timer) }, [refresh])
  useEffect(() => { if (!draftDirty) setDraftText(detail?.draft?.text || '') }, [detail?.draft?.id, detail?.draft?.text, draftDirty])

  function navigate(next: View, id?: number) {
    setView(next); setMobileMenu(false); setError('')
    const target = next === 'inbox' && id ? `/inbox/${id}` : `/${next}`
    window.history.pushState({}, '', target)
    if (id) setSelectedId(id)
    else if (next !== 'inbox' && next !== 'bookings') { setSelectedId(null); setDetail(null) }
  }
  async function select(id: number) {
    setDraftDirty(false)
    setSelectedId(id); navigate('inbox', id)
    try { setDetail(await api<Conversation>(`/api/conversations/${id}`)) }
    catch (err) { setError((err as Error).message) }
  }
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await fn(); await refresh() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  if (checking) return <div className="loading">Cargando AvantIA…</div>
  if (!user) return <Login onLogin={setUser} />

  const visibleItems = items.filter(item =>
    (view !== 'bookings' || item.kind === 'booking') &&
    (!statusFilter || item.status === statusFilter) &&
    (!channelFilter || item.channel === channelFilter) &&
    (!priorityFilter || item.priority === priorityFilter))
  const section = navigation.find(item => item.id === view)?.label || 'Resumen'
  const readOnlyMode = integrations?.mode?.read_only !== false

  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? 'sidebar-open' : ''}`}>
      <button className="brand" onClick={() => navigate('summary')} aria-label="Ir al resumen"><span className="brand-mark">A<span>·</span></span><span><strong>AVANTIA</strong><small>ATENCIÓN / HUB</small></span></button>
      <div className="nav-label">ESPACIO DE TRABAJO</div>
      <nav aria-label="Principal">{navigation.filter(item => item.id !== 'users' || user.role === 'admin').map(item =>
        <button key={item.id} className={`nav-item ${view === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>
          <span className="nav-code">{item.code}</span>{item.label}
          {item.id === 'inbox' && !!summary?.pending && <span className="nav-count">{summary.pending}</span>}
        </button>)}</nav>
      <div className="sidebar-bottom"><span className="status-dot" /> MODO SOLO LECTURA<br /><small>Correo intacto · salidas bloqueadas.</small></div>
    </aside>
    <div className="main-column">
      <header className="topbar"><button className="menu-button" onClick={() => setMobileMenu(!mobileMenu)} aria-label="Abrir menú">☰</button><div className="breadcrumb">AVANTIA <span>/</span> {section.toUpperCase()}</div><div className="top-user"><span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span><span>{user.username}<small>{user.role === 'admin' ? 'Administrador' : 'Operador'}</small></span><button className="text-button" onClick={() => { setToken(null); setUser(null) }}>Salir</button></div></header>
      <main className="workspace">
        <div className="page-heading"><div><p className="eyebrow">CENTRO DE OPERACIONES / {section.toUpperCase()}</p><h1>{section}</h1><p>{view === 'summary' ? 'El estado de todas las conversaciones, en un solo lugar.' : view === 'inbox' ? 'Revisa cada consulta antes de responder.' : view === 'bookings' ? 'Prerreservas que requieren seguimiento humano.' : view === 'imports' ? 'La disponibilidad depende de la última instantánea válida.' : 'Información operativa de AvantIA.'}</p></div><button className="button subtle" onClick={() => void refresh()} disabled={busy}>↻ Actualizar</button></div>
        {readOnlyMode && <div className="readonly-banner" role="status"><strong>MODO DESARROLLO · SOLO LECTURA</strong><span>IMAP se consulta sin modificar correos. Los envíos de email y WhatsApp están bloqueados.</span></div>}
        {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')} aria-label="Cerrar aviso">×</button></div>}
        {view === 'summary' && <div className="summary-grid">
          <div className="metric card"><span>PENDIENTES</span><strong>{summary?.pending ?? '—'}</strong><small>Consultas por atender</small><button onClick={() => navigate('inbox')}>Abrir bandeja →</button></div>
          <div className="metric card accent"><span>PRERRESERVAS</span><strong>{summary?.bookings ?? '—'}</strong><small>Booking · revisión humana</small><button onClick={() => navigate('bookings')}>Ver tareas →</button></div>
          <div className="metric card"><span>ENVÍOS INCIERTOS</span><strong>{summary?.delivery_unknown ?? '—'}</strong><small>Comprobar antes de reenviar</small></div>
          <section className="card queue-card"><div className="card-head"><div><p className="eyebrow">COLA ACTIVA</p><h2>Consultas recientes</h2></div><button className="text-button" onClick={() => navigate('inbox')}>Ver todas →</button></div>{items.length ? items.slice(0, 5).map(item => <button className="queue-row" key={item.id} onClick={() => void select(item.id)}><span className="queue-id">#{item.id}</span><span className="queue-name">{item.customer_name || item.customer_address || 'Cliente'}<small>{item.subject || label(item.channel)}</small></span><Badge value={item.status} /><span className="queue-date">{date(item.last_inbound_at)}</span></button>) : <Empty>Aún no hay consultas. Conecta un canal para empezar.</Empty>}</section>
          <section className="card status-card"><div className="card-head"><div><p className="eyebrow">FUENTE DE INVENTARIO</p><h2>Disponibilidad</h2></div></div><div className="status-line"><span className="status-dot" />Última importación</div><strong>{date(summary?.last_import_at)}</strong><p>{summary?.last_import_rows || 0} periodos importados. Las respuestas que usan datos caducados quedan para comprobación manual.</p><button className="button secondary" onClick={() => navigate('imports')}>Gestionar CSV →</button></section>
        </div>}
        {(view === 'inbox' || view === 'bookings') && <div className="inbox-layout"><section className="card inbox-list"><div className="card-head"><div><p className="eyebrow">{visibleItems.length} CONVERSACIONES</p><h2>{view === 'bookings' ? 'Prerreservas' : 'Bandeja de entrada'}</h2></div></div><div className="filters"><label>Estado<select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}><option value="">Todos</option>{['pending', 'queued', 'sending', 'sent', 'delivery_unknown', 'closed'].map(v => <option key={v} value={v}>{label(v)}</option>)}</select></label><label>Canal<select value={channelFilter} onChange={e => setChannelFilter(e.target.value)}><option value="">Todos</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option></select></label><label>Prioridad<select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)}><option value="">Todas</option><option value="high">Alta</option><option value="normal">Normal</option></select></label></div><div className="conversation-list">{visibleItems.length ? visibleItems.map(item => <button key={item.id} className={`conversation-row ${selectedId === item.id ? 'selected' : ''}`} onClick={() => void select(item.id)}><span className="conversation-row-top"><strong>{item.customer_name || item.customer_address || 'Cliente'}</strong><small>{date(item.last_inbound_at)}</small></span><span className="conversation-subject">{item.subject || `${label(item.channel)} · consulta #${item.id}`}</span><span className="row-foot"><Badge value={item.kind === 'booking' ? 'booking' : item.channel} /><Badge value={item.status} />{item.priority === 'high' && <span className="priority">ALTA</span>}</span></button>) : <Empty>No hay elementos con estos filtros.</Empty>}</div></section>
          <section className="card detail-panel">{detail && visibleItems.some(item => item.id === detail.id) ? <>
            <div className="detail-header"><div><p className="eyebrow">CONVERSACIÓN #{detail.id} / {label(detail.channel).toUpperCase()}</p><h2>{detail.customer_name || detail.customer_address || 'Cliente'}</h2><p>{detail.subject || 'Consulta de alojamiento'}</p></div><Badge value={detail.status} /></div>
            <div className="detail-toolbar"><label>Responsable<select value={detail.assigned_user_id ?? ''} onChange={e => void action(() => api(`/api/conversations/${detail.id}/assign`, { method: 'POST', body: JSON.stringify({ user_id: e.target.value ? Number(e.target.value) : null }) }))}><option value="">Sin asignar</option>{users.map(item => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label><span>Recibido {date(detail.last_inbound_at)}</span></div>
            <div className="message-feed">{detail.messages?.map(message => <div key={message.id} className={`message ${message.direction}`}><div className="message-meta">{message.direction === 'inbound' ? 'CLIENTE' : 'AVANTIA'} · {date(message.created_at)}</div><p>{message.body}</p>{message.source_url && <a href={message.source_url} target="_blank" rel="noreferrer">Abrir mensaje original ↗</a>}</div>)}</div>
            <div className="analysis-panel"><p className="eyebrow">ANÁLISIS / DATOS VERIFICADOS</p><div className="facts"><span>Alojamiento<strong>{String(detail.analysis.property_query || 'Sin identificar')}</strong></span><span>Entrada<strong>{String(detail.analysis.arrival || '—')}</strong></span><span>Salida<strong>{String(detail.analysis.departure || '—')}</strong></span><span>Huéspedes<strong>{String(detail.analysis.guests || '—')}</strong></span></div>{Boolean(detail.analysis.availability) && <><div className="availability-status">Disponibilidad: <Badge value={String((detail.analysis.availability as Record<string, unknown>).status)} />{(detail.analysis.availability as Record<string, unknown>).stale ? <span className="warning">CSV desactualizado</span> : null}</div>{Array.isArray((detail.analysis.availability as Record<string, unknown>).alternatives) && ((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).length > 0 && <div className="alternatives"><strong>Alternativas para revisión</strong>{((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).map((option, index) => <span key={index}>{option.name} · {option.capacity} plazas</span>)}</div>}</>}{Boolean(detail.analysis.availability_changed) && <p className="warning">La disponibilidad cambió desde que se creó el borrador. Revísalo antes de aprobar.</p>}{Boolean(detail.analysis.analysis_error) && <p className="warning">El análisis automático falló; comprobar manualmente.</p>}</div>
            {detail.kind === 'booking' ? <div className="review-panel"><p className="eyebrow">TAREA BOOKING</p><p>Comprueba la prerreserva en Avantio y realiza el seguimiento. AvantIA no confirma reservas automáticamente.</p><button className="button primary" disabled={busy || detail.booking_task?.status === 'closed'} onClick={() => detail.booking_task && void action(() => api(`/api/bookings/${detail.booking_task!.id}/close`, { method: 'POST' }))}>Marcar como gestionada</button></div> : <div className="review-panel"><div className="card-head"><div><p className="eyebrow">BORRADOR / REVISIÓN OBLIGATORIA</p><h3>Respuesta al cliente</h3></div></div><textarea aria-label="Borrador de respuesta" value={draftText} onChange={e => { setDraftText(e.target.value); setDraftDirty(true) }} disabled={busy || !['pending', 'error'].includes(detail.status)} rows={6} /><div className="review-actions"><button className="button secondary" disabled={busy || detail.status !== 'pending'} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false) })}>Guardar borrador</button><button className="button primary" disabled={readOnlyMode || busy || detail.status !== 'pending' || !draftText.trim()} title={readOnlyMode ? 'Desactivado en modo solo lectura' : undefined} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false); await api(`/api/conversations/${detail.id}/approve`, { method: 'POST' }) })}>{readOnlyMode ? 'Envío bloqueado' : 'Aprobar y enviar →'}</button><button className="text-button" disabled={busy || detail.status !== 'pending'} onClick={() => void action(() => api(`/api/conversations/${detail.id}/pending`, { method: 'POST' }))}>Dejar pendiente</button></div>{detail.status === 'delivery_unknown' && <div className="warning-box"><strong>Envío incierto.</strong> Comprueba en el canal si llegó antes de resolverlo.{user.role === 'admin' && <div className="review-actions"><button className="button secondary" onClick={() => { const note = window.prompt('¿Cómo verificaste que se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'sent', note }) })) }}>Confirmar enviado</button><button className="button secondary" onClick={() => { const note = window.prompt('¿Cómo verificaste que no se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'not_sent', note }) })) }}>No se envió</button></div>}</div>}{detail.draft?.error && <p className="error">{detail.draft.error}</p>}</div>}
            <details className="audit"><summary>Actividad y auditoría</summary>{detail.audit?.map((event, index) => <p key={index}>{date(event.created_at)} · {event.action} {event.actor_user_id ? `· usuario #${event.actor_user_id}` : ''}</p>)}</details>
          </> : <Empty>Selecciona una conversación para revisar su contenido y preparar la respuesta.</Empty>}</section></div>}
        {view === 'properties' && <section className="card table-card"><div className="card-head"><div><p className="eyebrow">INVENTARIO IMPORTADO</p><h2>Alojamientos</h2></div></div>{properties.length ? <div className="table-wrap"><table><thead><tr><th>ID</th><th>Alojamiento</th><th>Capacidad</th><th>Periodos</th></tr></thead><tbody>{properties.map(item => <tr key={item.id}><td>{item.id}</td><td>{item.name}</td><td>{item.capacity} plazas</td><td>{item.periods}</td></tr>)}</tbody></table></div> : <Empty>Importa un CSV válido para ver el catálogo.</Empty>}</section>}
        {view === 'imports' && <div className="settings-grid"><section className="card import-card"><p className="eyebrow">NUEVA INSTANTÁNEA</p><h2>Importar disponibilidad</h2><p>El archivo debe describir intervalos con salida exclusiva y estado disponible u ocupado. Una importación inválida no reemplaza la última válida.</p><ImportForm onDone={refresh} admin={user.role === 'admin'} /></section><section className="card"><p className="eyebrow">HISTORIAL</p><h2>Importaciones</h2>{imports.length ? imports.map(item => <div className="import-row" key={item.id}><div><strong>{item.filename}</strong><small>{date(item.created_at)} · {item.source} · {item.rows} filas</small>{item.error && <small className="error">{item.error}</small>}</div><Badge value={item.status} /></div>) : <Empty>Sin importaciones todavía.</Empty>}</section></div>}
        {view === 'integrations' && <div className="integration-grid">{integrations ? Object.entries(integrations).filter(([name]) => name !== 'mode').map(([name, value]) => <div className="card integration-card" key={name}><p className="eyebrow">CANAL / FUENTE</p><h2>{name.toUpperCase()}</h2><Badge value={value.configured === false || value.stale ? 'manual_check' : 'available'} /><p>{value.sending_enabled === false ? 'Envío bloqueado por modo de solo lectura.' : value.read_only ? `Solo lectura · buzón ${value.mailbox || ''}` : value.configured === false ? 'Pendiente de configurar.' : value.stale ? 'La fuente está desactualizada.' : 'Configurado o disponible.'}</p>{value.last_success_at && <small>Última conexión: {date(value.last_success_at)}</small>}{value.last_import_at && <small>Última carga: {date(value.last_import_at)}</small>}{value.last_error && <small className="error">{value.last_error}</small>}</div>) : <Empty>Cargando integraciones…</Empty>}</div>}
        {view === 'users' && user.role === 'admin' && <div className="settings-grid"><section className="card"><p className="eyebrow">ACCESO</p><h2>Operadores</h2>{users.map(item => <div className="import-row" key={item.id}><strong>{item.username}</strong><Badge value={item.role} /></div>)}</section><UserForm onDone={refresh} /></div>}
      </main>
    </div>
  </div>
}

function ImportForm({ onDone, admin }: { onDone: () => Promise<void>; admin: boolean }) {
  const [file, setFile] = useState<File | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!file) return
    setBusy(true); setMessage('')
    const form = new FormData(); form.set('file', file); form.set('mapping', JSON.stringify(mapping))
    try { const result = await api<{ status: string; rows: number; error: string | null }>('/api/imports', { method: 'POST', body: form }); setMessage(result.status === 'valid' ? `${result.rows} periodos importados correctamente.` : `Archivo rechazado: ${result.error}`); await onDone() }
    catch (err) { setMessage((err as Error).message) } finally { setBusy(false) }
  }
  return <form className="stack" onSubmit={submit}><label>Archivo CSV<input type="file" accept=".csv,text/csv" disabled={!admin} onChange={e => setFile(e.target.files?.[0] || null)} required /></label><div className="mapping"><p className="eyebrow">MAPEO DE COLUMNAS · DEJAR VACÍO SI COINCIDE</p>{Object.entries(fieldLabels).map(([field, title]) => <label key={field}>{title}<input placeholder={field} value={mapping[field] || ''} onChange={e => setMapping({ ...mapping, [field]: e.target.value })} /></label>)}</div><button className="button primary" disabled={!admin || !file || busy}>{busy ? 'Validando…' : 'Validar e importar →'}</button>{!admin && <p className="muted">Solo un administrador puede importar archivos.</p>}{message && <p role="status">{message}</p>}</form>
}

function UserForm({ onDone }: { onDone: () => Promise<void> }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator'); const [message, setMessage] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setMessage('')
    try { await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password, role }) }); setUsername(''); setPassword(''); setMessage('Usuario creado.'); await onDone() }
    catch (err) { setMessage((err as Error).message) }
  }
  return <section className="card"><p className="eyebrow">NUEVO ACCESO</p><h2>Crear usuario</h2><form className="stack" onSubmit={submit}><label>Usuario<input value={username} onChange={e => setUsername(e.target.value)} minLength={3} required /></label><label>Contraseña<input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></label><label>Rol<select value={role} onChange={e => setRole(e.target.value)}><option value="operator">Operador</option><option value="admin">Administrador</option></select></label><button className="button primary">Crear usuario</button>{message && <p role="status">{message}</p>}</form></section>
}
