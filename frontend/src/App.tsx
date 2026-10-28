import { DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, setToken, token } from './api'
import { Planning } from './Planning'
import { ImportHistory, SourceImportForm } from './SourceImports'
import type { Conversation, InboxResponse, InboxThread, ImportBatch, Property, Summary, User } from './types'
import { ArrowLeft, ArrowRight, CalendarDays, CalendarRange, Cable, ChevronDown, ChevronRight, House, Inbox, LayoutDashboard, LogOut, Menu, RefreshCw, Search, Upload, Users, X } from 'lucide-react'
import { Badge as UiBadge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Textarea } from './components/ui/textarea'

type View = 'summary' | 'inbox' | 'bookings' | 'properties' | 'planning' | 'imports' | 'integrations' | 'users'
const navigation: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'summary', label: 'Resumen', icon: LayoutDashboard },
  { id: 'inbox', label: 'Bandeja', icon: Inbox },
  { id: 'bookings', label: 'Prerreservas', icon: CalendarDays },
  { id: 'properties', label: 'Alojamientos', icon: House },
  { id: 'planning', label: 'Planning', icon: CalendarRange },
  { id: 'imports', label: 'Importaciones', icon: Upload },
  { id: 'integrations', label: 'Integraciones', icon: Cable },
  { id: 'users', label: 'Usuarios', icon: Users },
]
const fieldLabels: Record<string, string> = {
  property_id: 'ID alojamiento', property_name: 'Nombre', capacity: 'Capacidad',
  start_date: 'Entrada / inicio', end_date: 'Salida / fin', status: 'Estado',
}

function date(value?: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}
type DatePreset = 'today' | '7d' | '30d' | 'all' | 'custom'
type InboxGrouping = 'thread' | 'sender'
const INBOX_PAGE_SIZE = 50

function localMidnight(value: string, nextDay = false) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day + (nextDay ? 1 : 0)).toISOString()
}
function inboxDateBounds(preset: DatePreset, from: string, to: string) {
  if (preset === 'all') return { from: '', to: '' }
  if (preset === 'custom') return { from: from ? localMidnight(from) : '', to: to ? localMidnight(to, true) : '' }
  const today = new Date()
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)
  const daysBack = preset === 'today' ? 0 : preset === '7d' ? 6 : 29
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - daysBack)
  return { from: start.toISOString(), to: end.toISOString() }
}
function label(value: string) {
  return ({ pending: 'Pendiente', queued: 'En cola', sending: 'Enviando', sent: 'Enviado',
    delivery_unknown: 'Envío incierto', closed: 'Cerrado', available: 'Disponible',
    unavailable: 'No disponible', manual_check: 'Revisión manual',
    ambiguous_property: 'Alojamiento ambiguo', valid: 'Válido', invalid: 'Inválido',
    whatsapp: 'WhatsApp', email: 'Email', booking: 'Booking' } as Record<string, string>)[value] || value
}
function emailDocument(source: string) {
  const policy = "default-src 'none'; script-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; connect-src 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; media-src 'none'; base-uri 'none'"
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="referrer" content="no-referrer"><style>html,body{margin:0;padding:0;max-width:100%;overflow-wrap:anywhere}body{padding:12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#243a41}img{max-width:100%;height:auto}</style>${source}`
}
function Badge({ value }: { value: string }) { return <UiBadge className={`badge badge-${value}`}>{label(value)}</UiBadge> }
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
  return <div className="login-page"><Card className="login-card">
    <div className="brand-mark">A<span>·</span></div><p className="eyebrow">AVANTIA / ACCESO</p>
    <h1>Atención que<br />siempre llega.</h1>
    <p className="muted">La hospitalidad empieza mucho antes de abrir la puerta. Aquí, cada consulta encuentra su lugar.</p>
    <form onSubmit={submit} className="stack">
      <label>Usuario<Input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label>
      <label>Contraseña<Input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <Button className="button primary" disabled={busy}>{busy ? 'Accediendo…' : <>Entrar al panel <ArrowRight size={16} /></>}</Button>
    </form>
  </Card><div className="login-aside"><div className="login-aside-copy"><p className="eyebrow">UNA ESTANCIA EMPIEZA AQUÍ</p><h2>Un buen recibimiento<br />también se escribe.</h2><p>Conversaciones, reservas y disponibilidad, reunidas con cuidado.</p></div><div className="login-steps"><p><span>01</span> Recibir</p><p><span>02</span> Comprender</p><p><span>03</span> Revisar</p><p><span>04</span> Responder</p></div><div className="sun-arc" aria-hidden="true" /></div></div>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(!!token())
  const path = window.location.pathname
  const [view, setView] = useState<View>(path.startsWith('/inbox') ? 'inbox' : navigation.find(item => path === `/${item.id}`)?.id || 'summary')
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
  const [grouping, setGrouping] = useState<InboxGrouping>('thread')
  const [datePreset, setDatePreset] = useState<DatePreset>('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [searchText, setSearchText] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [inboxRows, setInboxRows] = useState<InboxResponse['items']>([])
  const [inboxTotal, setInboxTotal] = useState(0)
  const [inboxLoading, setInboxLoading] = useState(false)
  const [expandedSender, setExpandedSender] = useState<string | null>(null)
  const [senderThreads, setSenderThreads] = useState<Record<string, InboxResponse>>({})
  const [senderLoading, setSenderLoading] = useState<string | null>(null)
  const loadedInboxCount = useRef(0)
  const inboxRequestVersion = useRef(0)
  const senderRequestVersion = useRef(0)
  const inboxFilterVersion = useRef(0)
  const [mobileMenu, setMobileMenu] = useState(false)

  useEffect(() => {
    if (!token()) return
    api<User>('/api/me').then(setUser).catch(() => setToken(null)).finally(() => setChecking(false))
  }, [])

  const inboxParams = useCallback((offset: number, limit: number, groupBy: InboxGrouping = grouping, sender?: string) => {
    const params = new URLSearchParams({
      group_by: groupBy,
      offset: String(offset),
      limit: String(limit),
    })
    const bounds = inboxDateBounds(datePreset, dateFrom, dateTo)
    if (bounds.from) params.set('from', bounds.from)
    if (bounds.to) params.set('to', bounds.to)
    if (searchQuery) params.set('q', searchQuery)
    if (statusFilter) params.set('status', statusFilter)
    if (channelFilter) params.set('channel', channelFilter)
    if (priorityFilter) params.set('priority', priorityFilter)
    if (view === 'bookings') params.set('kind', 'booking')
    if (sender) params.set('sender', sender)
    return `/api/inbox?${params.toString()}`
  }, [grouping, datePreset, dateFrom, dateTo, searchQuery, statusFilter, channelFilter, priorityFilter, view])

  const loadInboxPage = useCallback(async (offset: number, append: boolean, limit = INBOX_PAGE_SIZE) => {
    if (!user || (view !== 'inbox' && view !== 'bookings')) return
    const requestVersion = ++inboxRequestVersion.current
    setInboxLoading(true)
    try {
      const response = await api<InboxResponse>(inboxParams(offset, limit, view === 'bookings' ? 'thread' : grouping))
      if (requestVersion !== inboxRequestVersion.current) return
      setInboxRows(current => append ? [...current, ...response.items] : response.items)
      setInboxTotal(response.total)
      loadedInboxCount.current = append ? offset + response.items.length : response.items.length
      setError('')
    } catch (err) { if (requestVersion === inboxRequestVersion.current) setError((err as Error).message) }
    finally { if (requestVersion === inboxRequestVersion.current) setInboxLoading(false) }
  }, [user, view, grouping, inboxParams])

  const reloadInbox = useCallback(async () => {
    if (!user || (view !== 'inbox' && view !== 'bookings')) return
    const requestVersion = ++inboxRequestVersion.current
    setInboxLoading(true)
    try {
      const pageCount = Math.max(1, Math.ceil(Math.max(INBOX_PAGE_SIZE, loadedInboxCount.current) / INBOX_PAGE_SIZE))
      const pages = await Promise.all(Array.from({ length: pageCount }, (_, index) =>
        api<InboxResponse>(inboxParams(index * INBOX_PAGE_SIZE, INBOX_PAGE_SIZE, view === 'bookings' ? 'thread' : grouping))))
      if (requestVersion !== inboxRequestVersion.current) return
      const rows = pages.flatMap(page => page.items)
      setInboxRows(rows)
      setInboxTotal(pages[0]?.total ?? 0)
      loadedInboxCount.current = rows.length
    } catch (err) {
      if (requestVersion === inboxRequestVersion.current) {
        setError((err as Error).message)
        throw err
      }
    }
    finally { if (requestVersion === inboxRequestVersion.current) setInboxLoading(false) }
  }, [user, view, grouping, inboxParams])

  const loadSenderThreads = useCallback(async (sender: string, offset = 0, append = false) => {
    const requestVersion = ++senderRequestVersion.current
    setSenderLoading(sender)
    try {
      const response = await api<InboxResponse>(inboxParams(offset, INBOX_PAGE_SIZE, 'thread', sender))
      if (requestVersion !== senderRequestVersion.current) return
      setSenderThreads(current => {
        const previous = current[sender]
        return { ...current, [sender]: {
          ...response,
          items: append && previous ? [...previous.items, ...response.items] : response.items,
        } }
      })
    } catch (err) { if (requestVersion === senderRequestVersion.current) setError((err as Error).message) }
    finally { if (requestVersion === senderRequestVersion.current) setSenderLoading(null) }
  }, [inboxParams])

  const refresh = useCallback(async () => {
    if (!user) return
    const requestedFilterVersion = inboxFilterVersion.current
    try {
      const [nextSummary, nextItems, nextUsers] = await Promise.all([
        api<Summary>('/api/summary'), api<Conversation[]>('/api/conversations?limit=5'), api<User[]>('/api/users'),
      ])
      const nextIntegrations = await api<Record<string, any>>('/api/integrations')
      setSummary(nextSummary); setItems(nextItems); setUsers(nextUsers)
      setIntegrations(nextIntegrations)
      if (requestedFilterVersion === inboxFilterVersion.current) await reloadInbox()
      if (view === 'properties') setProperties(await api<Property[]>('/api/properties'))
      if (view === 'imports') setImports(await api<ImportBatch[]>('/api/imports'))
      if (selectedId) {
        const nextDetail = await api<Conversation>(`/api/conversations/${selectedId}`)
        setDetail(nextDetail)
      }
      setError('')
    } catch (err) { setError((err as Error).message) }
  }, [user, view, selectedId, reloadInbox])

  useEffect(() => {
    inboxFilterVersion.current += 1
    inboxRequestVersion.current += 1
    senderRequestVersion.current += 1
    loadedInboxCount.current = 0
    setInboxRows([])
    setInboxTotal(0)
    setInboxLoading(false)
    setSenderLoading(null)
    setExpandedSender(null)
    setSenderThreads({})
  }, [view, grouping, datePreset, dateFrom, dateTo, searchQuery, statusFilter, channelFilter, priorityFilter])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 15000); return () => clearInterval(timer) }, [refresh])
  useEffect(() => { if (!draftDirty) setDraftText(detail?.draft?.text || '') }, [detail?.draft?.id, detail?.draft?.text, draftDirty])
  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(searchText.trim()), 250)
    return () => clearTimeout(timer)
  }, [searchText])

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

  const loadedThreads = [
    ...inboxRows.filter((item): item is InboxThread => item.type === 'thread'),
    ...Object.values(senderThreads).flatMap(response => response.items.filter((item): item is InboxThread => item.type === 'thread')),
  ]
  const selectedThreadLoaded = detail && loadedThreads.some(item => item.id === detail.id)
  const renderThread = (item: InboxThread) => <Button key={item.id} className={`conversation-row ${selectedId === item.id ? 'selected' : ''}`} onClick={() => void select(item.id)}>
    <span className="conversation-row-top"><strong>{item.customer_name || item.customer_address || 'Cliente'}</strong><small>{date(item.last_inbound_at)}</small></span>
    <span className="conversation-subject">{item.subject || `${label(item.channel)} · consulta #${item.id}`}</span>
    <span className="conversation-preview">{item.preview || 'Sin vista previa del último mensaje'}</span>
    <span className="row-foot"><Badge value={item.kind === 'booking' ? 'booking' : item.channel} /><Badge value={item.status} /><span className="message-count">{item.message_count} {item.message_count === 1 ? 'mensaje' : 'mensajes'}</span>{item.priority === 'high' && <span className="priority">ALTA</span>}</span>
  </Button>
  const clearInboxFilters = () => {
    setSearchText(''); setSearchQuery('')
    setDatePreset('all'); setDateFrom(''); setDateTo('')
    setStatusFilter(''); setChannelFilter(''); setPriorityFilter('')
  }
  const section = navigation.find(item => item.id === view)?.label || 'Resumen'
  const readOnlyMode = integrations?.mode?.read_only !== false

  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? 'sidebar-open' : ''}`}>
      <Button className="brand" onClick={() => navigate('summary')} aria-label="Ir al resumen"><span className="brand-mark">A<span>·</span></span><span><strong>AVANTIA</strong><small>HOSPITALIDAD CONECTADA</small></span></Button>
      <div className="nav-label">ESPACIO DE TRABAJO</div>
      <nav aria-label="Principal">{navigation.filter(item => item.id !== 'users' || user.role === 'admin').map(item =>
        <Button key={item.id} variant="ghost" className={`nav-item ${view === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>
          <span className="nav-icon"><item.icon size={18} strokeWidth={1.7} /></span>{item.label}
          {item.id === 'inbox' && !!summary?.pending && <span className="nav-count">{summary.pending}</span>}
        </Button>)}</nav>
      <div className="sidebar-bottom"><span className="status-dot" /> MODO SOLO LECTURA<br /><small>Correo intacto · salidas bloqueadas.</small></div>
    </aside>
    <div className="main-column">
      <header className="topbar"><Button variant="ghost" className="menu-button" onClick={() => setMobileMenu(!mobileMenu)} aria-label="Abrir menú"><Menu size={20} /></Button><div className="breadcrumb"><span className="breadcrumb-brand">AvantIA</span><ChevronRight size={14} /><span>{section}</span></div><div className="top-user"><span className="avatar">{user.username.slice(0, 1).toUpperCase()}</span><span>{user.username}<small>{user.role === 'admin' ? 'Administradora' : 'Operador'}</small></span><Button variant="ghost" className="text-button" onClick={() => { setToken(null); setUser(null) }}><LogOut size={15} /><span>Salir</span></Button></div></header>
      <main className="workspace">
        <div className="page-heading"><div><p className="eyebrow"><span className="heading-flower">✳</span> TU ESPACIO DE HOSPITALIDAD <span className="heading-slash">/</span> {section.toUpperCase()}</p><h1>{section}</h1><p>{view === 'summary' ? 'Cada conversación, en su sitio. La atención empieza aquí.' : view === 'inbox' ? 'Lee el contexto, revisa el borrador y decide cómo responder.' : view === 'bookings' ? 'Prerreservas que necesitan una mirada y seguimiento humano.' : view === 'imports' ? 'Fuentes y disponibilidad, siempre con su origen a la vista.' : view === 'planning' ? 'Una vista clara de las estancias y los huecos disponibles.' : 'La información operativa de AvantIA, reunida con cuidado.'}</p></div><Button className="button subtle" variant="outline" onClick={() => void refresh()} disabled={busy}><RefreshCw size={15} /> Actualizar</Button></div>
        {readOnlyMode && <div className="readonly-banner" role="status"><strong>MODO DESARROLLO · SOLO LECTURA</strong><span>IMAP se consulta sin modificar correos. Los envíos de email y WhatsApp están bloqueados.</span></div>}
        {error && <div className="error-banner" role="alert">{error}<Button onClick={() => setError('')} aria-label="Cerrar aviso">×</Button></div>}
        {view === 'summary' && <div className="summary-grid">
          <div className="metric card"><span>PENDIENTES</span><strong>{summary?.pending ?? '—'}</strong><small>Consultas por atender</small><Button onClick={() => navigate('inbox')}>Abrir bandeja →</Button></div>
          <div className="metric card accent"><span>PRERRESERVAS</span><strong>{summary?.bookings ?? '—'}</strong><small>Booking · revisión humana</small><Button onClick={() => navigate('bookings')}>Ver tareas →</Button></div>
          <div className="metric card"><span>ENVÍOS INCIERTOS</span><strong>{summary?.delivery_unknown ?? '—'}</strong><small>Comprobar antes de reenviar</small></div>
          <section className="card queue-card"><div className="card-head"><div><p className="eyebrow">COLA ACTIVA</p><h2>Consultas recientes</h2></div><Button className="text-button" onClick={() => navigate('inbox')}>Ver todas →</Button></div>{items.length ? items.slice(0, 5).map(item => <Button className="queue-row" key={item.id} onClick={() => void select(item.id)}><span className="queue-id">#{item.id}</span><span className="queue-name">{item.customer_name || item.customer_address || 'Cliente'}<small>{item.subject || label(item.channel)}</small></span><Badge value={item.status} /><span className="queue-date">{date(item.last_inbound_at)}</span></Button>) : <Empty>Aún no hay consultas. Conecta un canal para empezar.</Empty>}</section>
          <section className="card status-card"><div className="card-head"><div><p className="eyebrow">FUENTE DE INVENTARIO</p><h2>Disponibilidad</h2></div></div><div className="status-line"><span className="status-dot" />Última importación</div><strong>{date(summary?.last_import_at)}</strong><p>{summary?.last_import_rows || 0} periodos importados. Las respuestas que usan datos caducados quedan para comprobación manual.</p><Button className="button secondary" onClick={() => navigate('imports')}>Gestionar CSV →</Button></section>
        </div>}
        {(view === 'inbox' || view === 'bookings') && <div className={`inbox-layout ${selectedThreadLoaded ? 'detail-open' : ''}`}>
          <section className="card inbox-list">
            <div className="inbox-list-heading">
              <div><p className="eyebrow">{inboxTotal} {grouping === 'sender' && view === 'inbox' ? 'GRUPOS Y CHATS' : 'CONVERSACIONES'}</p><h2>{view === 'bookings' ? 'Prerreservas' : 'Bandeja de entrada'}</h2></div>
              {view === 'inbox' && <div className="inbox-group-toggle" role="group" aria-label="Agrupar bandeja">
                <Button className={grouping === 'thread' ? 'active' : ''} aria-pressed={grouping === 'thread'} onClick={() => setGrouping('thread')}>Hilos</Button>
                <Button className={grouping === 'sender' ? 'active' : ''} aria-pressed={grouping === 'sender'} onClick={() => setGrouping('sender')}>Remitentes</Button>
              </div>}
            </div>
            <div className="inbox-controls">
              <label className="inbox-search"><span>Buscar</span><span className="inbox-search-field"><Search size={16} aria-hidden="true" /><Input type="search" aria-label="Buscar por remitente, nombre, asunto o mensaje" placeholder="Remitente, asunto o mensaje…" value={searchText} onChange={event => setSearchText(event.target.value)} /></span></label>
              <div className="inbox-date-controls">
                <div className="date-presets" role="group" aria-label="Filtrar por fecha">
                  {([['today', 'Hoy'], ['7d', '7 días'], ['30d', '30 días'], ['all', 'Todo']] as [DatePreset, string][]).map(([preset, caption]) => <Button key={preset} className={`date-preset ${datePreset === preset ? 'active' : ''}`} aria-pressed={datePreset === preset} onClick={() => { setDatePreset(preset); setDateFrom(''); setDateTo('') }}>{caption}</Button>)}
                </div>
                <label>Desde<Input type="date" value={dateFrom} onChange={event => { setDateFrom(event.target.value); setDatePreset('custom') }} /></label>
                <label>Hasta<Input type="date" value={dateTo} onChange={event => { setDateTo(event.target.value); setDatePreset('custom') }} /></label>
              </div>
              <div className="filters inbox-filters">
                <label>Estado<select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">Todos</option>{['pending', 'queued', 'sending', 'sent', 'delivery_unknown', 'closed'].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label>
                <label>Canal<select value={channelFilter} onChange={event => setChannelFilter(event.target.value)}><option value="">Todos</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option></select></label>
                <label>Prioridad<select value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)}><option value="">Todas</option><option value="high">Alta</option><option value="normal">Normal</option></select></label>
                <Button className="clear-filters" variant="ghost" onClick={clearInboxFilters} disabled={!searchText && datePreset === 'all' && !dateFrom && !dateTo && !statusFilter && !channelFilter && !priorityFilter}><X size={14} /> Limpiar</Button>
              </div>
            </div>
            <div className="conversation-list">
              {inboxLoading && !inboxRows.length ? <Empty>Cargando conversaciones…</Empty> : inboxRows.length ? inboxRows.map(item => item.type === 'thread' ? renderThread(item) : <div className="sender-group" key={item.sender}>
                <Button className="sender-group-heading" aria-expanded={expandedSender === item.sender} onClick={() => {
                  if (expandedSender === item.sender) { setExpandedSender(null); return }
                  setExpandedSender(item.sender)
                  if (!senderThreads[item.sender]) void loadSenderThreads(item.sender)
                }}>
                  <span className="sender-group-copy"><strong>{item.display_name}</strong><small>{item.address} · {item.conversation_count} {item.conversation_count === 1 ? 'hilo' : 'hilos'} · última actividad {date(item.last_inbound_at)}</small></span>
                  <ChevronDown size={17} className={expandedSender === item.sender ? 'expanded' : ''} aria-hidden="true" />
                </Button>
                {expandedSender === item.sender && <div className="sender-group-threads">
                  {senderLoading === item.sender && !senderThreads[item.sender] ? <Empty>Cargando hilos…</Empty> : senderThreads[item.sender]?.items.filter((thread): thread is InboxThread => thread.type === 'thread').map(renderThread)}
                  {senderThreads[item.sender] && senderThreads[item.sender].items.length === 0 && <Empty>No hay hilos de este remitente con los filtros actuales.</Empty>}
                  {senderThreads[item.sender] && senderThreads[item.sender].items.length < senderThreads[item.sender].total && <Button className="load-more sender-load-more" variant="outline" onClick={() => void loadSenderThreads(item.sender, senderThreads[item.sender].items.length, true)} disabled={senderLoading === item.sender}>Cargar más hilos</Button>}
                </div>}
              </div>) : <Empty>No hay conversaciones con estos filtros.</Empty>}
              {inboxRows.length < inboxTotal && <Button className="load-more" variant="outline" onClick={() => void loadInboxPage(inboxRows.length, true)} disabled={inboxLoading}>{inboxLoading ? 'Cargando…' : `Cargar más (${inboxRows.length} de ${inboxTotal})`}</Button>}
            </div>
          </section>
          <section className="card detail-panel">{detail && selectedThreadLoaded ? <>
            <div className="detail-header"><div><Button variant="ghost" className="mobile-back-inbox" onClick={() => { setSelectedId(null); setDetail(null); window.history.pushState({}, '', '/inbox') }}><ArrowLeft size={16} /> Bandeja</Button><p className="eyebrow">CONVERSACIÓN #{detail.id} / {label(detail.channel).toUpperCase()}</p><h2>{detail.customer_name || detail.customer_address || 'Cliente'}</h2><p>{detail.subject || 'Consulta de alojamiento'}</p></div><Badge value={detail.status} /></div>
            <div className="detail-toolbar"><span>Recibido {date(detail.last_inbound_at)}</span></div>
            <div className={`message-feed ${detail.channel === 'email' ? 'email-feed' : ''}`}>{detail.messages?.map(message => <article key={message.id} className={`message ${message.direction} ${detail.channel === 'email' ? 'email-message' : ''}`}>
              <div className="message-meta">{message.direction === 'inbound' ? 'RECIBIDO' : 'AVANTIA'} · {date(message.created_at)}</div>
              {detail.channel === 'email' ? <>
                <dl className="email-headers">
                  <div><dt>De</dt><dd>{message.sender || detail.customer_address || '—'}</dd></div>
                  {message.recipient && <div><dt>Para</dt><dd>{message.recipient}</dd></div>}
                  <div><dt>Asunto</dt><dd>{message.subject || detail.subject || '(sin asunto)'}</dd></div>
                </dl>
                {message.html_body ? <iframe className="email-render-frame" title={`Cuerpo del email: ${message.subject || detail.subject || 'sin asunto'}`} sandbox="" referrerPolicy="no-referrer" srcDoc={emailDocument(message.html_body)} /> : <div className="email-text-body">{message.body || '(correo sin texto)'}</div>}
              </> : <p>{message.body}</p>}
              {message.source_url && <a href={message.source_url} target="_blank" rel="noreferrer">Abrir mensaje original ↗</a>}
            </article>)}</div>
            <div className="analysis-panel"><p className="eyebrow">ANÁLISIS / DATOS VERIFICADOS</p><div className="facts"><span>Alojamiento<strong>{String(detail.analysis.property_query || 'Sin identificar')}</strong></span><span>Entrada<strong>{String(detail.analysis.arrival || '—')}</strong></span><span>Salida<strong>{String(detail.analysis.departure || '—')}</strong></span><span>Huéspedes<strong>{String(detail.analysis.guests || '—')}</strong></span></div>{Boolean(detail.analysis.availability) && <><div className="availability-status">Disponibilidad: <Badge value={String((detail.analysis.availability as Record<string, unknown>).status)} />{(detail.analysis.availability as Record<string, unknown>).stale ? <span className="warning">CSV desactualizado</span> : null}</div>{Array.isArray((detail.analysis.availability as Record<string, unknown>).alternatives) && ((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).length > 0 && <div className="alternatives"><strong>Alternativas para revisión</strong>{((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).map((option, index) => <span key={index}>{option.name} · {option.capacity} plazas</span>)}</div>}</>}{Boolean(detail.analysis.availability_changed) && <p className="warning">La disponibilidad cambió desde que se creó el borrador. Revísalo antes de aprobar.</p>}{Boolean(detail.analysis.analysis_error) && <p className="warning">El análisis automático falló; comprobar manualmente.</p>}</div>
            {detail.kind === 'booking' ? <div className="review-panel"><p className="eyebrow">TAREA BOOKING</p><p>Comprueba la prerreserva en Avantio y realiza el seguimiento. AvantIA no confirma reservas automáticamente.</p><Button className="button primary" disabled={busy || detail.booking_task?.status === 'closed'} onClick={() => detail.booking_task && void action(() => api(`/api/bookings/${detail.booking_task!.id}/close`, { method: 'POST' }))}>Marcar como gestionada</Button></div> : <div className="review-panel"><div className="card-head"><div><p className="eyebrow">BORRADOR / REVISIÓN OBLIGATORIA</p><h3>Respuesta al cliente</h3></div></div><Textarea aria-label="Borrador de respuesta" value={draftText} onChange={e => { setDraftText(e.target.value); setDraftDirty(true) }} disabled={busy || !['pending', 'error'].includes(detail.status)} rows={6} /><div className="review-actions"><Button className="button secondary" disabled={busy || detail.status !== 'pending'} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false) })}>Guardar borrador</Button><Button className="button primary" disabled={readOnlyMode || busy || detail.status !== 'pending' || !draftText.trim()} title={readOnlyMode ? 'Desactivado en modo solo lectura' : undefined} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false); await api(`/api/conversations/${detail.id}/approve`, { method: 'POST' }) })}>{readOnlyMode ? 'Envío bloqueado' : 'Aprobar y enviar →'}</Button><Button className="text-button" disabled={busy || detail.status !== 'pending'} onClick={() => void action(() => api(`/api/conversations/${detail.id}/pending`, { method: 'POST' }))}>Dejar pendiente</Button></div>{detail.status === 'delivery_unknown' && <div className="warning-box"><strong>Envío incierto.</strong> Comprueba en el canal si llegó antes de resolverlo.{user.role === 'admin' && <div className="review-actions"><Button className="button secondary" onClick={() => { const note = window.prompt('¿Cómo verificaste que se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'sent', note }) })) }}>Confirmar enviado</Button><Button className="button secondary" onClick={() => { const note = window.prompt('¿Cómo verificaste que no se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'not_sent', note }) })) }}>No se envió</Button></div>}</div>}{detail.draft?.error && <p className="error">{detail.draft.error}</p>}</div>}
            <details className="audit"><summary>Actividad y auditoría</summary>{detail.audit?.map((event, index) => <p key={index}>{date(event.created_at)} · {event.action} {event.actor_user_id ? `· usuario #${event.actor_user_id}` : ''}</p>)}</details>
          </> : <Empty>Selecciona una conversación para revisar su contenido y preparar la respuesta.</Empty>}</section></div>}
        {view === 'properties' && <section className="card table-card"><div className="card-head"><div><p className="eyebrow">CATÁLOGO VERSIONADO</p><h2>Alojamientos</h2></div></div>{properties.length ? <div className="table-wrap"><table><thead><tr><th>ID</th><th>Alojamiento</th><th>Estado</th><th>Clase</th><th>Capacidad</th><th>Edificio / ubicación</th><th>Origen</th></tr></thead><tbody>{properties.map(item => <tr key={item.id}><td>{item.id}</td><td>{item.name}</td><td>{item.active ? 'Activo' : 'Inactivo'}</td><td>{item.category}</td><td>{item.capacity} plazas</td><td>{[item.building, item.city, item.address].filter(Boolean).join(' · ') || '—'}</td><td>#{item.batch_id} · fila {item.source_row}</td></tr>)}</tbody></table></div> : <Empty>Importa una exportación de propiedades para ver el catálogo.</Empty>}</section>}
        {view === 'planning' && <Planning />}
        {view === 'imports' && <div className="settings-grid"><section className="card import-card"><p className="eyebrow">CATÁLOGO Y RESERVAS</p><h2>Importación supervisada</h2><p>Valida la exportación, revisa cambios e incidencias y confirma la nueva instantánea. Los CSV originales no se conservan.</p><SourceImportForm onDone={refresh} admin={user.role === 'admin'} /></section><section className="card import-card"><p className="eyebrow">INTERVALOS</p><h2>Importar disponibilidad</h2><p>El archivo describe intervalos con salida exclusiva y estado disponible u ocupado. Una importación inválida conserva la última válida.</p><ImportForm onDone={refresh} admin={user.role === 'admin'} /></section><section className="card import-history-card"><ImportHistory imports={imports} /></section></div>}
        {view === 'integrations' && <div className="integration-grid">{integrations ? Object.entries(integrations).filter(([name]) => name !== 'mode').map(([name, value]) => <div className="card integration-card" key={name}><p className="eyebrow">CANAL / FUENTE</p><h2>{name.toUpperCase()}</h2><Badge value={value.configured === false || value.stale || (name === 'imap' && !value.connected) ? 'manual_check' : 'available'} /><p>{value.sending_enabled === false ? 'Envío bloqueado por modo de solo lectura.' : value.read_only ? value.connected ? `Conectado en solo lectura · buzón ${value.mailbox || ''}` : value.configured ? `Esperando primera conexión · buzón ${value.mailbox || ''}` : 'Falta configurar el acceso IMAP.' : value.configured === false ? 'Pendiente de configurar.' : value.stale ? 'La fuente está desactualizada.' : 'Configurado o disponible.'}</p>{value.last_success_at && <small>Última conexión: {date(value.last_success_at)}</small>}{value.last_import_at && <small>Última carga: {date(value.last_import_at)}</small>}{value.last_error && <small className="error">{value.last_error}</small>}</div>) : <Empty>Cargando integraciones…</Empty>}</div>}
        {view === 'users' && user.role === 'admin' && <div className="settings-grid"><section className="card"><p className="eyebrow">ACCESO</p><h2>Operadores</h2>{users.map(item => <div className="import-row" key={item.id}><strong>{item.username}</strong><Badge value={item.role} /></div>)}</section><UserForm onDone={refresh} /></div>}
      </main>
    </div>
  </div>
}

function ImportForm({ onDone, admin }: { onDone: () => Promise<void>; admin: boolean }) {
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [results, setResults] = useState<{ name: string; message: string; valid: boolean }[]>([])
  const [busy, setBusy] = useState(false)
  function addFiles(selected: FileList | File[]) {
    const csvs = Array.from(selected).filter(candidate => candidate.name.toLowerCase().endsWith('.csv') || candidate.type === 'text/csv')
    setFiles(current => [...current, ...csvs.filter(candidate => !current.some(existing => existing.name === candidate.name && existing.size === candidate.size && existing.lastModified === candidate.lastModified))])
    setResults([]); setMessage('')
  }
  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false)
    if (admin && !busy) addFiles(event.dataTransfer.files)
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!files.length) return
    setBusy(true); setMessage(''); setResults([])
    const completed: { name: string; message: string; valid: boolean }[] = []
    for (const file of files) {
      const form = new FormData(); form.set('file', file); form.set('mapping', JSON.stringify(mapping))
      try {
        const result = await api<{ status: string; rows: number; error: string | null }>('/api/imports', { method: 'POST', body: form })
        completed.push({ name: file.name, message: result.status === 'valid' ? `${result.rows} periodos importados` : `Rechazado: ${result.error}`, valid: result.status === 'valid' })
      } catch (err) { completed.push({ name: file.name, message: (err as Error).message, valid: false }) }
      setResults([...completed])
    }
    setFiles([]); setBusy(false)
    if (completed.some(item => item.valid)) await onDone()
  }
  return <form className="stack" onSubmit={submit}><div className={`csv-dropzone ${dragging ? 'is-dragging' : ''}`} onDragEnter={event => { event.preventDefault(); setDragging(true) }} onDragOver={event => event.preventDefault()} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}><strong>Suelta aquí tus archivos CSV</strong><span>Cada CSV válido sustituye la instantánea anterior; quedará activo el último</span><label className="csv-picker">Elegir archivos<input type="file" accept=".csv,text/csv" multiple disabled={!admin || busy} onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} /></label></div>{files.length > 0 && <div className="csv-queue" aria-label="Archivos pendientes de importación"><div className="csv-queue-heading"><strong>Cola de importación</strong><span>{files.length} {files.length === 1 ? 'archivo' : 'archivos'}</span></div>{files.map((queued, index) => <div className="csv-queue-row" key={`${queued.name}-${queued.size}-${queued.lastModified}`}><span><strong>{queued.name}</strong><small>{(queued.size / 1024).toLocaleString('es-ES', { maximumFractionDigits: 0 })} KB</small></span>{!busy && <Button type="button" className="text-button" onClick={() => setFiles(current => current.filter((_, row) => row !== index))}>Quitar</Button>}</div>)}</div>}<div className="mapping"><p className="eyebrow">MAPEO DE COLUMNAS · DEJAR VACÍO SI COINCIDE</p>{Object.entries(fieldLabels).map(([field, title]) => <label key={field}>{title}<input placeholder={field} value={mapping[field] || ''} onChange={e => setMapping({ ...mapping, [field]: e.target.value })} /></label>)}</div><Button className="button primary" disabled={!admin || !files.length || busy}>{busy ? 'Importando archivos…' : `Validar e importar ${files.length || ''} ${files.length === 1 ? 'archivo' : 'archivos'} →`}</Button>{results.length > 0 && <div className="csv-results" role="status">{results.map((item, index) => <p className={item.valid ? 'csv-result-valid' : 'csv-result-invalid'} key={`${item.name}-${index}`}><strong>{item.name}</strong> · {item.message}</p>)}</div>}{!admin && <p className="muted">Solo un administrador puede importar archivos.</p>}{message && <p role="status">{message}</p>}</form>
}

function UserForm({ onDone }: { onDone: () => Promise<void> }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator'); const [message, setMessage] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setMessage('')
    try { await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password, role }) }); setUsername(''); setPassword(''); setMessage('Usuario creado.'); await onDone() }
    catch (err) { setMessage((err as Error).message) }
  }
  return <section className="card"><p className="eyebrow">NUEVO ACCESO</p><h2>Crear usuario</h2><form className="stack" onSubmit={submit}><label>Usuario<input value={username} onChange={e => setUsername(e.target.value)} minLength={3} required /></label><label>Contraseña<input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></label><label>Rol<select value={role} onChange={e => setRole(e.target.value)}><option value="operator">Operador</option><option value="admin">Administrador</option></select></label><Button className="button primary">Crear usuario</Button>{message && <p role="status">{message}</p>}</form></section>
}
