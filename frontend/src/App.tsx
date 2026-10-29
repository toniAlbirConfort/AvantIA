import { DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, setToken, token } from './api'
import { Planning } from './Planning'
import { ImportHistory, SourceImportForm } from './SourceImports'
import type { Conversation, InboxResponse, InboxThread, ImportBatch, Message, Property, Summary, User } from './types'
import { ArrowLeft, ArrowRight, CalendarDays, CalendarRange, Cable, ChevronDown, ChevronRight, House, Inbox, LayoutDashboard, LogOut, Menu, RefreshCw, Search, Upload, Users, X } from 'lucide-react'
import { EmailPreviewDialog } from './EmailPreviewDialog'
import { EmailMessageContent } from './EmailMessageContent'
import { Badge as UiBadge } from './components/ui/badge'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'
import { Textarea } from './components/ui/textarea'
import { BrandLogo, BrandMark } from './components/BrandLogo'
import { GowaIntegrationCard } from './GowaIntegrationCard'

type View = 'summary' | 'inbox' | 'bookings' | 'properties' | 'planning' | 'imports' | 'integrations' | 'users'
const navigation: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'summary', label: 'Resumen', icon: LayoutDashboard },
  { id: 'planning', label: 'Planning', icon: CalendarRange },
  { id: 'inbox', label: 'Bandeja', icon: Inbox },
  { id: 'bookings', label: 'Prerreservas', icon: CalendarDays },
  { id: 'properties', label: 'Alojamientos', icon: House },
  { id: 'imports', label: 'Importaciones', icon: Upload },
  { id: 'users', label: 'Usuarios', icon: Users },
  { id: 'integrations', label: 'Integraciones', icon: Cable },
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
    syncing: 'Sincronizando', sync_error: 'Error', synced: 'Al día',
    whatsapp: 'WhatsApp', email: 'Email', booking: 'Booking' } as Record<string, string>)[value] || value
}
const badgeStyles: Record<string, string> = {
  pending: 'bg-warning-bg text-warning-ink', queued: 'bg-info-bg text-info-ink', sending: 'bg-info-bg text-info-ink',
  sent: 'bg-success-bg text-success-ink', valid: 'bg-success-bg text-success-ink', available: 'bg-success-bg text-success-ink',
  closed: 'bg-muted text-muted-foreground', booking: 'bg-info-bg text-info-ink', whatsapp: 'bg-success-bg text-success-ink',
  email: 'bg-info-bg text-info-ink', invalid: 'bg-error-bg text-error-ink', unavailable: 'bg-error-bg text-error-ink',
  syncing: 'bg-info-bg text-info-ink', synced: 'bg-success-bg text-success-ink', sync_error: 'bg-error-bg text-error-ink',
  delivery_unknown: 'bg-warning-bg text-warning-ink', manual_check: 'bg-warning-bg text-warning-ink',
  ambiguous_property: 'bg-warning-bg text-warning-ink', high: 'bg-warning-bg text-warning-ink', admin: 'bg-info-bg text-info-ink',
  operator: 'bg-muted text-muted-foreground',
}
function Badge({ value }: { value: string }) { return <UiBadge className={badgeStyles[value] || 'bg-muted text-muted-foreground'}>{label(value)}</UiBadge> }
function Empty({ children }: { children: React.ReactNode }) { return <div className="grid min-h-28 place-items-center px-6 py-10 text-center text-sm leading-7 text-muted-foreground">{children}</div> }

function integrationTitle(name: string) {
  return name === 'avantio' ? 'Reservas Avantio' : name.toUpperCase()
}

function integrationBadge(name: string, value: any) {
  if (name !== 'avantio') {
    return value.configured === false || value.stale || (name === 'imap' && !value.connected) ? 'manual_check' : 'available'
  }
  if (!value.configured) return 'manual_check'
  if (value.status === 'running') return 'syncing'
  if (value.status === 'failed') return 'sync_error'
  if (!value.last_success_at) return 'pending'
  const intervalMs = Number(value.interval_hours || 6) * 60 * 60 * 1000
  return Date.now() - new Date(value.last_success_at).getTime() > intervalMs * 1.5 ? 'manual_check' : 'synced'
}

function integrationDescription(name: string, value: any) {
  if (name === 'avantio') {
    if (!value.configured) return 'Falta configurar AVANTIO_EXPORT_URL en el .env del servidor.'
    if (value.status === 'running') return 'Chrome dedicado está exportando el CSV y actualizando el planning.'
    if (value.status === 'failed') return 'Falló la última sincronización. La última instantánea válida sigue activa.'
    return `Exportación con Chrome dedicado en Windows cada ${value.interval_hours} h.`
  }
  if (value.sending_enabled === false) return 'Envío bloqueado por modo de solo lectura.'
  if (value.read_only) return value.connected ? `Conectado en solo lectura · buzón ${value.mailbox || ''}` : value.configured ? `Esperando primera conexión · buzón ${value.mailbox || ''}` : 'Falta configurar el acceso IMAP.'
  if (value.configured === false) return 'Pendiente de configurar.'
  if (value.stale) return 'La fuente está desactualizada.'
  return 'Configurado o disponible.'
}

function IntegrationCard({ name, value }: { name: string; value: any }) {
  return <Card className="p-5" key={name}>
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CANAL / FUENTE</p>
    <h2 className="mb-3 text-xl font-semibold">{integrationTitle(name)}</h2>
    <Badge value={integrationBadge(name, value)} />
    <p className="my-4 text-sm leading-6 text-muted-foreground">{integrationDescription(name, value)}</p>
    {name === 'avantio' && value.runner === 'windows_chrome_cdp' && <small className="block text-xs text-muted-foreground">Exportador: Chrome dedicado del servidor</small>}
    {value.last_attempt_at && <small className="block text-xs text-muted-foreground">Último intento: {date(value.last_attempt_at)}</small>}
    {value.last_success_at && <small className="mt-1 block text-xs text-muted-foreground">{name === 'avantio' ? 'Última sincronización' : 'Última conexión'}: {date(value.last_success_at)}{name === 'avantio' ? ` · ${value.row_count || 0} filas · lote #${value.last_batch_id ?? '—'}` : ''}</small>}
    {name !== 'avantio' && value.last_import_at && <small className="mt-1 block text-xs text-muted-foreground">Última carga: {date(value.last_import_at)}</small>}
    {value.last_error && <p className="mt-2 break-words text-sm text-error-ink">{value.last_error}</p>}
  </Card>
}

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
  return <main className="grid min-h-screen bg-background lg:grid-cols-2">
    <Card className="flex min-h-screen flex-col justify-center rounded-none border-0 px-7 py-12 shadow-none sm:px-12 lg:px-[clamp(3rem,8vw,7rem)]">
      <div className="mb-8"><BrandMark className="size-12" /><span className="sr-only">AvantIA</span></div>
      <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-primary">AVANTIA / ACCESO</p>
      <h1 className="mb-5 text-4xl font-semibold leading-tight text-foreground sm:text-5xl">Atención que<br />siempre llega.</h1>
      <p className="mb-9 max-w-md leading-7 text-muted-foreground">La hospitalidad empieza mucho antes de abrir la puerta. Aquí, cada consulta encuentra su lugar.</p>
      <form onSubmit={submit} className="grid max-w-md gap-5">
        <label className="grid gap-2 text-sm font-medium">Usuario<Input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label>
        <label className="grid gap-2 text-sm font-medium">Contraseña<Input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
        {error && <Alert variant="destructive">{error}</Alert>}
        <Button disabled={busy}>{busy ? 'Accediendo…' : <>Entrar al panel <ArrowRight size={16} /></>}</Button>
      </form>
    </Card>
    <section className="hidden flex-col justify-between bg-card px-12 py-16 lg:flex xl:px-24" aria-label="Acerca de AvantIA">
      <div className="max-w-xl"><p className="mb-4 text-xs font-semibold uppercase tracking-wide text-primary">UNA ESTANCIA EMPIEZA AQUÍ</p><h2 className="mb-5 text-4xl font-semibold leading-tight text-foreground xl:text-5xl">Un buen recibimiento<br />también se escribe.</h2><p className="max-w-sm leading-7 text-muted-foreground">Conversaciones, reservas y disponibilidad, reunidas con cuidado.</p></div>
      <div className="grid max-w-xl grid-cols-2 gap-x-8">{['Recibir', 'Comprender', 'Revisar', 'Responder'].map((step, index) => <p key={step} className="border-t border-border py-4 text-lg"><span className="mr-3 text-sm text-primary">0{index + 1}</span>{step}</p>)}</div>
    </section>
  </main>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [checking, setChecking] = useState(!!token())
  const path = window.location.pathname
  const [view, setView] = useState<View>(path.startsWith('/inbox') ? 'inbox' : path.startsWith('/bookings') ? 'bookings' : navigation.find(item => path === `/${item.id}`)?.id || 'summary')
  const [selectedId, setSelectedId] = useState<number | null>(Number(path.split('/')[2]) || null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [items, setItems] = useState<Conversation[]>([])
  const [detail, setDetail] = useState<Conversation | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [properties, setProperties] = useState<Property[]>([])
  const [propertySearch, setPropertySearch] = useState('')
  const [propertyStatusFilter, setPropertyStatusFilter] = useState('active')
  const [propertyCategoryFilter, setPropertyCategoryFilter] = useState('')
  const [propertyCityFilter, setPropertyCityFilter] = useState('')
  const [imports, setImports] = useState<ImportBatch[]>([])
  const [integrations, setIntegrations] = useState<Record<string, any> | null>(null)
  const [expandedEmail, setExpandedEmail] = useState<Message | null>(null)
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
    const target = (next === 'inbox' || next === 'bookings') && id ? `/${next}/${id}` : `/${next}`
    window.history.pushState({}, '', target)
    if (id) setSelectedId(id)
    else if (next !== 'inbox' && next !== 'bookings') { setSelectedId(null); setDetail(null) }
  }
  async function select(id: number) {
    setDraftDirty(false)
    setDetail(null)
    navigate(view === 'bookings' ? 'bookings' : 'inbox', id)
    try { setDetail(await api<Conversation>(`/api/conversations/${id}`)) }
    catch (err) { setError((err as Error).message) }
  }
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await fn(); await refresh() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  if (checking) return <main className="grid min-h-screen place-items-center gap-4 bg-background p-6 text-center"><div className="grid justify-items-center gap-4"><Skeleton className="size-10 rounded-full" /><p className="text-sm text-muted-foreground">Cargando AvantIA…</p></div></main>
  if (!user) return <Login onLogin={setUser} />

  const loadedThreads = [
    ...inboxRows.filter((item): item is InboxThread => item.type === 'thread'),
    ...Object.values(senderThreads).flatMap(response => response.items.filter((item): item is InboxThread => item.type === 'thread')),
  ]
  const selectedThreadLoaded = detail && loadedThreads.some(item => item.id === detail.id)
  const renderThread = (item: InboxThread) => <Button key={item.id} variant="ghost" className={`h-auto w-full flex-col items-stretch justify-start gap-2 rounded-none border-b border-border px-4 py-4 text-left text-foreground whitespace-normal hover:bg-accent ${selectedId === item.id ? 'border-l-4 border-l-primary bg-accent pl-3' : ''}`} onClick={() => void select(item.id)}>
    <span className="flex items-center justify-between gap-3"><strong className="truncate text-sm">{item.customer_name || item.customer_address || 'Cliente'}</strong><small className="shrink-0 text-xs text-muted-foreground">{date(item.last_inbound_at)}</small></span>
    {item.channel === 'email' && item.customer_name && item.customer_address && <small className="block truncate text-xs text-muted-foreground">{item.customer_address}</small>}
    <span className="block truncate text-sm font-medium text-foreground">{item.subject || `${label(item.channel)} · consulta #${item.id}`}</span>
    <span className={item.channel === 'email' ? 'line-clamp-2 whitespace-normal text-sm leading-5 text-muted-foreground' : 'block truncate text-sm text-muted-foreground'}>{item.preview || 'Sin vista previa del último mensaje'}</span>
    <span className="flex flex-wrap items-center gap-2"><Badge value={item.kind === 'booking' ? 'booking' : item.channel} /><Badge value={item.status} /><span className="ml-auto text-xs text-muted-foreground">{item.message_count} {item.message_count === 1 ? 'mensaje' : 'mensajes'}</span>{item.priority === 'high' && <span className="text-xs font-semibold text-warning-ink">ALTA</span>}</span>
  </Button>
  const clearInboxFilters = () => {
    setSearchText(''); setSearchQuery('')
    setDatePreset('all'); setDateFrom(''); setDateTo('')
    setStatusFilter(''); setChannelFilter(''); setPriorityFilter('')
  }
  const section = navigation.find(item => item.id === view)?.label || 'Resumen'
  const readOnlyMode = integrations?.mode?.read_only !== false
  const propertyCategories = [...new Set(properties.map(item => item.category).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, 'es-ES'))
  const propertyCities = [...new Set(properties.map(item => item.city).filter((city): city is string => !!city))]
    .sort((left, right) => left.localeCompare(right, 'es-ES'))
  const propertyQuery = propertySearch.trim().toLocaleLowerCase('es-ES')
  const filteredProperties = properties.filter(item => {
    const searchable = `${item.name} ${item.id}`.toLocaleLowerCase('es-ES')
    return (!propertyQuery || searchable.includes(propertyQuery))
      && (propertyStatusFilter === 'all' || item.active === (propertyStatusFilter === 'active'))
      && (!propertyCategoryFilter || item.category === propertyCategoryFilter)
      && (!propertyCityFilter || item.city === propertyCityFilter)
  })
  const propertyFiltersChanged = !!propertySearch || propertyStatusFilter !== 'active' || !!propertyCategoryFilter || !!propertyCityFilter
  const clearPropertyFilters = () => {
    setPropertySearch('')
    setPropertyStatusFilter('active')
    setPropertyCategoryFilter('')
    setPropertyCityFilter('')
  }

  return <div className="min-h-screen bg-background text-foreground md:flex">
    {mobileMenu && <button type="button" className="fixed inset-0 z-30 bg-background/80 md:hidden" onClick={() => setMobileMenu(false)} aria-label="Cerrar menú" />}
    <aside className={`fixed inset-y-0 left-0 z-40 flex h-screen w-64 flex-col border-r border-brand-charcoal bg-brand-charcoal p-4 text-white transition-transform motion-reduce:transition-none md:sticky md:top-0 md:z-10 md:translate-x-0 ${mobileMenu ? 'translate-x-0' : '-translate-x-full'}`}>
      <Button variant="ghost" className="mb-6 min-h-16 justify-start gap-3 rounded-none border-b border-white/15 px-2 pb-5 text-left text-white hover:bg-white/10 hover:text-white" onClick={() => navigate('summary')} aria-label="Ir al resumen"><BrandLogo className="text-white" /></Button>
      <div className="mb-3 px-3 text-[0.65rem] font-semibold uppercase tracking-wide text-white/65">ESPACIO DE TRABAJO</div>
      <nav className="grid gap-1" aria-label="Principal">{navigation.filter(item => item.id !== 'users' || user.role === 'admin').map(item => <Button key={item.id} variant="ghost" className={`min-h-11 w-full justify-start gap-3 px-3 text-left ${view === item.id ? 'bg-brand-orange text-white hover:bg-brand-orange/90 hover:text-white' : 'text-white/75 hover:bg-white/10 hover:text-white'}`} onClick={() => navigate(item.id)}><item.icon size={18} strokeWidth={1.7} /><span>{item.label}</span>{item.id === 'inbox' && !!summary?.pending && <span className={`ml-auto rounded-full px-2 py-0.5 text-xs ${view === item.id ? 'bg-background text-primary' : 'bg-white/15 text-white'}`}>{summary.pending}</span>}</Button>)}</nav>
      <div className="mt-auto border-t border-white/15 pt-4 text-xs leading-6 text-white/75"><span className="mr-2 inline-block size-2 rounded-full bg-brand-turquoise" />MODO SOLO LECTURA<br /><small className="pl-4">Correo intacto · salidas bloqueadas.</small></div>
    </aside>
    <div className="min-w-0 flex-1">
      <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-4 border-b border-border bg-card px-4 sm:px-6 lg:px-10">
        <div className="flex min-w-0 items-center gap-3"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileMenu(!mobileMenu)} aria-label="Abrir menú"><Menu size={20} /></Button><div className="flex items-center gap-2 truncate text-sm text-muted-foreground"><strong className="text-foreground">AvantIA</strong><ChevronRight size={14} /><span className="truncate">{section}</span></div></div>
        <div className="flex shrink-0 items-center gap-3 text-sm"><span className="grid size-9 place-items-center rounded-full bg-accent font-semibold text-accent-foreground">{user.username.slice(0, 1).toUpperCase()}</span><span className="hidden sm:block">{user.username}<small className="block text-xs text-muted-foreground">{user.role === 'admin' ? 'Administradora' : 'Operador'}</small></span><Button variant="ghost" size="sm" className="gap-2" onClick={() => { setToken(null); setUser(null) }}><LogOut size={15} /><span className="hidden sm:inline">Salir</span></Button></div>
      </header>
      <main className="mx-auto w-full max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-10 lg:py-9">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4"><div><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-primary">TU ESPACIO DE HOSPITALIDAD <span className="px-1 text-muted-foreground">/</span> {section.toUpperCase()}</p><h1 className="mb-2 text-3xl font-semibold leading-tight sm:text-4xl">{section}</h1><p className="max-w-3xl text-sm leading-6 text-muted-foreground">{view === 'summary' ? 'Cada conversación, en su sitio. La atención empieza aquí.' : view === 'inbox' ? 'Lee el contexto, revisa el borrador y decide cómo responder.' : view === 'bookings' ? 'Prerreservas que necesitan una mirada y seguimiento humano.' : view === 'imports' ? 'Fuentes y disponibilidad, siempre con su origen a la vista.' : view === 'planning' ? 'Una vista clara de las estancias y los huecos disponibles.' : 'La información operativa de AvantIA, reunida con cuidado.'}</p></div><Button variant="outline" onClick={() => void refresh()} disabled={busy}><RefreshCw size={15} /> Actualizar</Button></div>
        {readOnlyMode && <Alert variant="info" role="status" className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-1"><strong className="text-xs">MODO DESARROLLO · SOLO LECTURA</strong><span>IMAP se consulta sin modificar correos. Los envíos de email y WhatsApp están bloqueados.</span></Alert>}
        {error && <Alert variant="destructive" className="mb-5 flex items-center justify-between gap-4"><span>{error}</span><Button variant="ghost" size="icon" onClick={() => setError('')} aria-label="Cerrar aviso"><X size={16} /></Button></Alert>}
        <EmailPreviewDialog message={expandedEmail} fallbackSubject={detail?.subject} onClose={() => setExpandedEmail(null)} />

        {view === 'summary' && <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-12">
          {[{ title: 'PENDIENTES', value: summary?.pending ?? '—', description: 'Consultas por atender', action: 'Abrir bandeja →', onClick: () => navigate('inbox') }, { title: 'PRERRESERVAS', value: summary?.bookings ?? '—', description: 'Booking · revisión humana', action: 'Ver tareas →', onClick: () => navigate('bookings') }, { title: 'ENVÍOS INCIERTOS', value: summary?.delivery_unknown ?? '—', description: 'Comprobar antes de reenviar' }].map((metric, index) => <Card key={metric.title} className={`relative overflow-hidden p-5 sm:p-6 xl:col-span-4 ${index === 1 ? 'border-primary/30' : ''}`}><span className="text-xs font-semibold tracking-wide text-muted-foreground">{metric.title}</span><strong className="my-3 block text-5xl font-semibold tabular-nums text-foreground">{metric.value}</strong><small className="block text-sm text-muted-foreground">{metric.description}</small>{metric.action && <Button variant="link" className="mt-3 h-auto p-0" onClick={metric.onClick}>{metric.action}</Button>}</Card>)}
          <Card className="p-5 sm:p-6 xl:col-span-7"><div className="mb-4 flex items-start justify-between gap-3"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">COLA ACTIVA</p><h2 className="text-xl font-semibold">Consultas recientes</h2></div><Button variant="link" size="sm" className="shrink-0 px-1" onClick={() => navigate('inbox')}>Ver todas →</Button></div>{items.length ? items.slice(0, 5).map(item => <Button variant="ghost" className="h-auto w-full justify-start gap-3 rounded-none border-t border-border px-1 py-3 text-left whitespace-normal" key={item.id} onClick={() => void select(item.id)}><span className="text-xs text-primary">#{item.id}</span><span className="min-w-0 flex-1 truncate text-sm">{item.customer_name || item.customer_address || 'Cliente'}<small className="block truncate text-xs text-muted-foreground">{item.subject || label(item.channel)}</small></span><Badge value={item.status} /><span className="hidden shrink-0 text-xs text-muted-foreground lg:inline">{date(item.last_inbound_at)}</span></Button>) : <Empty>Aún no hay consultas. Conecta un canal para empezar.</Empty>}</Card>
          <Card className="p-5 sm:p-6 xl:col-span-5"><div className="mb-4"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">FUENTE DE INVENTARIO</p><h2 className="text-xl font-semibold">Disponibilidad</h2></div><div className="border-t border-border pt-4 text-sm text-muted-foreground"><span className="mr-2 inline-block size-2 rounded-full bg-primary" />Última importación</div><strong className="my-2 block text-lg">{date(summary?.last_import_at)}</strong><p className="mb-4 text-sm leading-6 text-muted-foreground">{summary?.last_import_rows || 0} periodos importados. Las respuestas que usan datos caducados quedan para comprobación manual.</p><Button variant="secondary" onClick={() => navigate('imports')}>Gestionar CSV →</Button></Card>
        </div>}

        {(view === 'inbox' || view === 'bookings') && <div className="grid gap-4 lg:grid-cols-[minmax(23rem,5fr)_minmax(0,7fr)]">
          <section className={`min-w-0 overflow-hidden rounded-xl border border-border bg-card ${selectedThreadLoaded ? 'hidden lg:block' : ''}`}>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4 sm:p-5"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">{inboxTotal} {grouping === 'sender' && view === 'inbox' ? 'GRUPOS Y CHATS' : 'CONVERSACIONES'}</p><h2 className="text-lg font-semibold">{view === 'bookings' ? 'Prerreservas' : 'Bandeja de entrada'}</h2></div>{view === 'inbox' && <div className="flex rounded-md border border-border bg-background p-1" role="group" aria-label="Agrupar bandeja"><Button variant={grouping === 'thread' ? 'secondary' : 'ghost'} size="sm" aria-pressed={grouping === 'thread'} onClick={() => setGrouping('thread')}>Hilos</Button><Button variant={grouping === 'sender' ? 'secondary' : 'ghost'} size="sm" aria-pressed={grouping === 'sender'} onClick={() => setGrouping('sender')}>Remitentes</Button></div>}</div>
            <div className="grid gap-3 border-b border-border p-3 sm:p-4">
              <label className="grid gap-1.5 text-xs font-medium">Buscar<span className="relative"><Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" type="search" aria-label="Buscar por remitente, nombre, asunto o mensaje" placeholder="Remitente, asunto o mensaje…" value={searchText} onChange={event => setSearchText(event.target.value)} /></span></label>
              <div className="grid grid-cols-2 items-end gap-2 sm:grid-cols-4"><div className="col-span-2 flex flex-wrap gap-1 sm:col-span-2" role="group" aria-label="Filtrar por fecha">{([['today', 'Hoy'], ['7d', '7 días'], ['30d', '30 días'], ['all', 'Todo']] as [DatePreset, string][]).map(([preset, caption]) => <Button key={preset} variant={datePreset === preset ? 'secondary' : 'outline'} size="sm" aria-pressed={datePreset === preset} onClick={() => { setDatePreset(preset); setDateFrom(''); setDateTo('') }}>{caption}</Button>)}</div><label className="grid gap-1 text-xs font-medium">Desde<Input className="h-9 px-2 text-xs" type="date" value={dateFrom} onChange={event => { setDateFrom(event.target.value); setDatePreset('custom') }} /></label><label className="grid gap-1 text-xs font-medium">Hasta<Input className="h-9 px-2 text-xs" type="date" value={dateTo} onChange={event => { setDateTo(event.target.value); setDatePreset('custom') }} /></label></div>
              <div className="grid grid-cols-2 items-end gap-2 sm:grid-cols-4"><label className="grid gap-1 text-xs font-medium">Estado<Select className="h-9 px-2 text-xs" value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">Todos</option>{['pending', 'queued', 'sending', 'sent', 'delivery_unknown', 'closed'].map(value => <option key={value} value={value}>{label(value)}</option>)}</Select></label><label className="grid gap-1 text-xs font-medium">Canal<Select className="h-9 px-2 text-xs" value={channelFilter} onChange={event => setChannelFilter(event.target.value)}><option value="">Todos</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option></Select></label><label className="grid gap-1 text-xs font-medium">Prioridad<Select className="h-9 px-2 text-xs" value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)}><option value="">Todas</option><option value="high">Alta</option><option value="normal">Normal</option></Select></label><Button className="h-9" variant="ghost" size="sm" onClick={clearInboxFilters} disabled={!searchText && datePreset === 'all' && !dateFrom && !dateTo && !statusFilter && !channelFilter && !priorityFilter}><X size={14} /> Limpiar</Button></div>
            </div>
            <div className="max-h-[calc(100vh-24rem)] min-h-72 overflow-y-auto sm:max-h-[calc(100vh-26rem)]">
              {inboxLoading && !inboxRows.length ? <Empty>Cargando conversaciones…</Empty> : inboxRows.length ? inboxRows.map(item => item.type === 'thread' ? renderThread(item) : <div className="border-b border-border" key={item.sender}>
                <Button variant="ghost" className="h-auto min-h-16 w-full justify-between gap-3 rounded-none px-4 py-3 text-left whitespace-normal" aria-expanded={expandedSender === item.sender} onClick={() => { if (expandedSender === item.sender) { setExpandedSender(null); return }; setExpandedSender(item.sender); if (!senderThreads[item.sender]) void loadSenderThreads(item.sender) }}><span className="grid min-w-0 gap-1"><strong className="truncate text-sm">{item.display_name}</strong><small className="truncate text-xs text-muted-foreground">{item.address} · {item.conversation_count} {item.conversation_count === 1 ? 'hilo' : 'hilos'} · última actividad {date(item.last_inbound_at)}</small></span><ChevronDown size={17} className={`shrink-0 transition-transform ${expandedSender === item.sender ? 'rotate-180' : ''}`} aria-hidden="true" /></Button>
                {expandedSender === item.sender && <div className="border-t border-border bg-background">{senderLoading === item.sender && !senderThreads[item.sender] ? <Empty>Cargando hilos…</Empty> : senderThreads[item.sender]?.items.filter((thread): thread is InboxThread => thread.type === 'thread').map(renderThread)}{senderThreads[item.sender] && senderThreads[item.sender].items.length === 0 && <Empty>No hay hilos de este remitente con los filtros actuales.</Empty>}{senderThreads[item.sender] && senderThreads[item.sender].items.length < senderThreads[item.sender].total && <Button className="m-3 w-[calc(100%-1.5rem)]" variant="outline" onClick={() => void loadSenderThreads(item.sender, senderThreads[item.sender].items.length, true)} disabled={senderLoading === item.sender}>Cargar más hilos</Button>}</div>}
              </div>) : <Empty>No hay conversaciones con estos filtros.</Empty>}
              {inboxRows.length < inboxTotal && <Button className="m-3 w-[calc(100%-1.5rem)]" variant="outline" onClick={() => void loadInboxPage(inboxRows.length, true)} disabled={inboxLoading}>{inboxLoading ? 'Cargando…' : `Cargar más (${inboxRows.length} de ${inboxTotal})`}</Button>}
            </div>
          </section>
          <section className={`min-h-[32rem] min-w-0 overflow-hidden rounded-xl border border-border bg-card ${selectedThreadLoaded ? '' : 'hidden lg:block'}`}>{detail && selectedThreadLoaded ? <>
            <div className="flex items-start justify-between gap-3 border-b border-border p-4 sm:p-6"><div className="min-w-0"><Button variant="outline" size="sm" className="mb-3 lg:hidden" onClick={() => { setSelectedId(null); setDetail(null); window.history.pushState({}, '', view === 'bookings' ? '/bookings' : '/inbox') }}><ArrowLeft size={16} /> {view === 'bookings' ? 'Prerreservas' : 'Bandeja'}</Button><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CONVERSACIÓN #{detail.id} / {label(detail.channel).toUpperCase()}</p><h2 className="truncate text-xl font-semibold">{detail.customer_name || detail.customer_address || 'Cliente'}</h2><p className="mt-1 truncate text-sm text-muted-foreground">{detail.subject || 'Consulta de alojamiento'}</p></div><Badge value={detail.status} /></div>
            <div className="border-b border-border bg-background px-4 py-3 text-xs text-muted-foreground sm:px-6">Recibido {date(detail.last_inbound_at)}</div>
            <div className={`grid max-h-[70vh] gap-3 overflow-auto bg-background p-4 sm:p-6 ${detail.channel === 'email' ? 'email-feed' : ''}`}>{detail.messages?.map(message => <article key={message.id} className={`w-fit max-w-[90%] overflow-hidden rounded-lg border border-border p-3 text-sm leading-6 ${message.direction === 'outbound' ? 'ml-auto bg-accent' : 'bg-card'} ${detail.channel === 'email' ? 'w-full max-w-4xl p-0' : ''}`}>
              <div className="mb-2 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">{message.direction === 'inbound' ? 'RECIBIDO' : 'AVANTIA'} · {date(message.created_at)}</div>
              {detail.channel === 'email' ? <EmailMessageContent conversationId={detail.id} message={message} fallbackSender={detail.customer_address} fallbackSubject={detail.subject} onExpand={setExpandedEmail} /> : <p className="m-0 whitespace-pre-wrap break-words">{message.body}</p>}
              {message.source_url && <a className="mt-2 inline-block text-sm text-primary underline underline-offset-4" href={message.source_url} target="_blank" rel="noreferrer">Abrir mensaje original ↗</a>}
            </article>)}</div>
            <div className="border-y border-border p-4 sm:p-6"><p className="mb-3 text-xs font-semibold uppercase tracking-wide text-primary">ANÁLISIS / DATOS VERIFICADOS</p><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[['Alojamiento', String(detail.analysis.property_query || 'Sin identificar')], ['Entrada', String(detail.analysis.arrival || '—')], ['Salida', String(detail.analysis.departure || '—')], ['Huéspedes', String(detail.analysis.guests || '—')]].map(([name, value]) => <div key={name} className="min-w-0"><span className="block text-xs text-muted-foreground">{name}</span><strong className="mt-1 block break-words text-sm">{value}</strong></div>)}</div>{Boolean(detail.analysis.availability) && <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">Disponibilidad: <Badge value={String((detail.analysis.availability as Record<string, unknown>).status)} />{(detail.analysis.availability as Record<string, unknown>).stale ? <span className="text-warning-ink">CSV desactualizado</span> : null}</div>}{Array.isArray((detail.analysis.availability as Record<string, unknown> | undefined)?.alternatives) && ((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).length > 0 && <div className="mt-4 text-sm"><strong>Alternativas para revisión</strong><div className="mt-2 flex flex-wrap gap-2">{((detail.analysis.availability as Record<string, unknown>).alternatives as { name: string; capacity: number }[]).map((option, index) => <span key={index} className="rounded-md bg-accent px-2 py-1 text-xs">{option.name} · {option.capacity} plazas</span>)}</div></div>}{Boolean(detail.analysis.availability_changed) && <p className="mt-3 text-sm text-warning-ink">La disponibilidad cambió desde que se creó el borrador. Revísalo antes de aprobar.</p>}{Boolean(detail.analysis.analysis_error) && <p className="mt-3 text-sm text-warning-ink">El análisis automático falló; comprobar manualmente.</p>}</div>
            {detail.kind === 'booking' ? <div className="grid gap-3 p-4 sm:p-6"><p className="text-xs font-semibold uppercase tracking-wide text-primary">TAREA BOOKING</p><p className="text-sm leading-6 text-muted-foreground">Comprueba la prerreserva en Avantio y realiza el seguimiento. AvantIA no confirma reservas automáticamente.</p><Button className="w-fit" disabled={busy || detail.booking_task?.status === 'closed'} onClick={() => detail.booking_task && void action(() => api(`/api/bookings/${detail.booking_task!.id}/close`, { method: 'POST' }))}>Marcar como gestionada</Button></div> : <div className="grid gap-4 p-4 sm:p-6"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">BORRADOR / REVISIÓN OBLIGATORIA</p><h3 className="text-lg font-semibold">Respuesta al cliente</h3></div><Textarea aria-label="Borrador de respuesta" value={draftText} onChange={e => { setDraftText(e.target.value); setDraftDirty(true) }} disabled={busy || !['pending', 'error'].includes(detail.status)} rows={6} /><div className="flex flex-wrap items-center gap-2"><Button variant="secondary" disabled={busy || detail.status !== 'pending'} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false) })}>Guardar borrador</Button><Button disabled={readOnlyMode || busy || detail.status !== 'pending' || !draftText.trim()} title={readOnlyMode ? 'Desactivado en modo solo lectura' : undefined} onClick={() => void action(async () => { await api(`/api/conversations/${detail.id}/draft`, { method: 'PUT', body: JSON.stringify({ text: draftText, expected_draft_id: detail.draft?.id }) }); setDraftDirty(false); await api(`/api/conversations/${detail.id}/approve`, { method: 'POST' }) })}>{readOnlyMode ? 'Envío bloqueado' : 'Aprobar y enviar →'}</Button><Button variant="link" disabled={busy || detail.status !== 'pending'} onClick={() => void action(() => api(`/api/conversations/${detail.id}/pending`, { method: 'POST' }))}>Dejar pendiente</Button></div>{detail.status === 'delivery_unknown' && <Alert variant="warning"><strong>Envío incierto.</strong> Comprueba en el canal si llegó antes de resolverlo.{user.role === 'admin' && <div className="mt-3 flex flex-wrap gap-2"><Button variant="secondary" size="sm" onClick={() => { const note = window.prompt('¿Cómo verificaste que se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'sent', note }) })) }}>Confirmar enviado</Button><Button variant="secondary" size="sm" onClick={() => { const note = window.prompt('¿Cómo verificaste que no se envió?'); if (note) void action(() => api(`/api/conversations/${detail.id}/resolve-delivery`, { method: 'POST', body: JSON.stringify({ outcome: 'not_sent', note }) })) }}>No se envió</Button></div>}</Alert>}{detail.draft?.error && <Alert variant="destructive">{detail.draft.error}</Alert>}</div>}
            <details className="border-t border-border p-4 text-sm text-muted-foreground sm:px-6"><summary className="cursor-pointer font-semibold text-primary">Actividad y auditoría</summary>{detail.audit?.map((event, index) => <p key={index} className="mt-3">{date(event.created_at)} · {event.action} {event.actor_user_id ? `· usuario #${event.actor_user_id}` : ''}</p>)}</details>
          </> : <Empty>Selecciona una conversación para revisar su contenido y preparar la respuesta.</Empty>}</section>
        </div>}

        {view === 'properties' && <Card className="overflow-hidden p-4 sm:p-6">
          <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
            <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CATÁLOGO VERSIONADO</p><h2 className="text-xl font-semibold">Alojamientos</h2></div>
            <p className="text-sm text-muted-foreground" aria-live="polite">{filteredProperties.length} de {properties.length} alojamientos</p>
          </div>
          <div className="mb-5 grid gap-3 rounded-lg border border-border bg-background p-3 sm:grid-cols-2 xl:grid-cols-[minmax(14rem,2fr)_repeat(3,minmax(10rem,1fr))_auto]">
            <label className="grid gap-1.5 text-xs font-medium">Buscar alojamiento<Input type="search" aria-label="Buscar alojamiento por nombre o ID" placeholder="Nombre o ID…" value={propertySearch} onChange={event => setPropertySearch(event.target.value)} /></label>
            <label className="grid gap-1.5 text-xs font-medium">Estado<Select value={propertyStatusFilter} onChange={event => setPropertyStatusFilter(event.target.value)}><option value="active">Solo activos</option><option value="inactive">Solo inactivos</option><option value="all">Todos</option></Select></label>
            <label className="grid gap-1.5 text-xs font-medium">Clase<Select value={propertyCategoryFilter} onChange={event => setPropertyCategoryFilter(event.target.value)}><option value="">Cualquier clase</option>{propertyCategories.map(category => <option key={category} value={category}>{category}</option>)}</Select></label>
            <label className="grid gap-1.5 text-xs font-medium">Población<Select value={propertyCityFilter} onChange={event => setPropertyCityFilter(event.target.value)}><option value="">Cualquier población</option>{propertyCities.map(city => <option key={city} value={city}>{city}</option>)}</Select></label>
            <Button type="button" className="self-end" variant="ghost" size="sm" onClick={clearPropertyFilters} disabled={!propertyFiltersChanged}><X size={14} /> Limpiar</Button>
          </div>
          {properties.length === 0
            ? <Empty>Importa una exportación de propiedades para ver el catálogo.</Empty>
            : filteredProperties.length === 0
              ? <Empty>No hay alojamientos con esos filtros. <Button type="button" variant="link" onClick={clearPropertyFilters}>Restablecer filtros</Button></Empty>
              : <div className="max-w-full overflow-auto"><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Alojamiento</TableHead><TableHead>Estado</TableHead><TableHead>Clase</TableHead><TableHead>Capacidad</TableHead><TableHead>Edificio / ubicación</TableHead></TableRow></TableHeader><TableBody>{filteredProperties.map(item => <TableRow key={item.id}><TableCell>{item.id}</TableCell><TableCell>{item.name}</TableCell><TableCell>{item.active ? 'Activo' : 'Inactivo'}</TableCell><TableCell>{item.category}</TableCell><TableCell>{item.capacity} plazas</TableCell><TableCell>{[item.building, item.city, item.address].filter(Boolean).join(' · ') || '—'}</TableCell></TableRow>)}</TableBody></Table></div>}
        </Card>}
        {view === 'planning' && <Planning />}
        {view === 'imports' && <div className="grid items-start gap-4 xl:grid-cols-2"><Card className="p-4 sm:p-6"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CATÁLOGO Y RESERVAS</p><h2 className="mb-3 text-xl font-semibold">Importación supervisada</h2><p className="mb-5 text-sm leading-6 text-muted-foreground">Valida la exportación, revisa cambios e incidencias y confirma la nueva instantánea. Los CSV originales no se conservan.</p><SourceImportForm onDone={refresh} admin={user.role === 'admin'} /></Card><Card className="p-4 sm:p-6"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">INTERVALOS</p><h2 className="mb-3 text-xl font-semibold">Importar disponibilidad</h2><p className="mb-5 text-sm leading-6 text-muted-foreground">El archivo describe intervalos con salida exclusiva y estado disponible u ocupado. Una importación inválida conserva la última válida.</p><ImportForm onDone={refresh} admin={user.role === 'admin'} /></Card><Card className="p-4 sm:p-6 xl:col-span-2"><ImportHistory imports={imports} /></Card></div>}
        {view === 'integrations' && <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{integrations ? <>{Object.entries(integrations).filter(([name]) => name !== 'mode' && (name !== 'whatsapp' || user.role === 'admin')).map(([name, value]) => name === 'whatsapp' ? <GowaIntegrationCard key={name} /> : <IntegrationCard key={name} name={name} value={value} />)}</> : <Empty>Cargando integraciones…</Empty>}</div>}
        {view === 'users' && user.role === 'admin' && <div className="grid items-start gap-4 xl:grid-cols-2"><Card className="p-5 sm:p-6"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">ACCESO</p><h2 className="mb-4 text-xl font-semibold">Operadores</h2>{users.map(item => <div className="flex items-center justify-between gap-3 border-t border-border py-3" key={item.id}><strong className="text-sm">{item.username}</strong><Badge value={item.role} /></div>)}</Card><UserForm onDone={refresh} /></div>}
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
  return <form className="grid gap-5" onSubmit={submit}>
    <div className={`flex min-h-36 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-5 text-center transition-colors ${dragging ? 'border-primary bg-accent' : 'border-border bg-background'}`} onDragEnter={event => { event.preventDefault(); setDragging(true) }} onDragOver={event => event.preventDefault()} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}><strong className="text-sm">Suelta aquí tus archivos CSV</strong><span className="text-xs text-muted-foreground">Cada CSV válido sustituye la instantánea anterior; quedará activo el último</span><label className="mt-2 inline-flex min-h-10 cursor-pointer items-center rounded-md border border-border bg-card px-4 py-2 text-sm font-medium hover:bg-accent focus-within:ring-2 focus-within:ring-ring">Elegir archivos<input className="sr-only" type="file" accept=".csv,text/csv" multiple disabled={!admin || busy} onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} /></label></div>
    {files.length > 0 && <div className="overflow-hidden rounded-lg border border-border" aria-label="Archivos pendientes de importación"><div className="flex items-center justify-between gap-3 bg-background px-3 py-2 text-sm"><strong>Cola de importación</strong><span className="text-xs text-muted-foreground">{files.length} {files.length === 1 ? 'archivo' : 'archivos'}</span></div>{files.map((queued, index) => <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2.5 text-sm" key={`${queued.name}-${queued.size}-${queued.lastModified}`}><span className="min-w-0 break-all"><strong>{queued.name}</strong><small className="mt-1 block text-xs text-muted-foreground">{(queued.size / 1024).toLocaleString('es-ES', { maximumFractionDigits: 0 })} KB</small></span>{!busy && <Button type="button" variant="link" size="sm" onClick={() => setFiles(current => current.filter((_, row) => row !== index))}>Quitar</Button>}</div>)}</div>}
    <div className="grid gap-3 sm:grid-cols-2"><p className="col-span-full mb-0 text-xs font-semibold uppercase tracking-wide text-primary">MAPEO DE COLUMNAS · DEJAR VACÍO SI COINCIDE</p>{Object.entries(fieldLabels).map(([field, title]) => <label className="grid gap-1.5 text-xs font-medium" key={field}>{title}<Input placeholder={field} value={mapping[field] || ''} onChange={e => setMapping({ ...mapping, [field]: e.target.value })} /></label>)}</div>
    <Button disabled={!admin || !files.length || busy}>{busy ? 'Importando archivos…' : `Validar e importar ${files.length || ''} ${files.length === 1 ? 'archivo' : 'archivos'} →`}</Button>
    {results.length > 0 && <div className="grid gap-2" role="status">{results.map((item, index) => <Alert variant={item.valid ? 'success' : 'destructive'} role="status" key={`${item.name}-${index}`}><strong>{item.name}</strong> · {item.message}</Alert>)}</div>}
    {!admin && <p className="text-sm text-muted-foreground">Solo un administrador puede importar archivos.</p>}
    {message && <Alert variant="info" role="status">{message}</Alert>}
  </form>
}

function UserForm({ onDone }: { onDone: () => Promise<void> }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator'); const [message, setMessage] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault(); setMessage('')
    try { await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password, role }) }); setUsername(''); setPassword(''); setMessage('Usuario creado.'); await onDone() }
    catch (err) { setMessage((err as Error).message) }
  }
  return <Card className="grid gap-4 p-5 sm:p-6"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">NUEVO ACCESO</p><h2 className="text-xl font-semibold">Crear usuario</h2></div><form className="grid gap-4" onSubmit={submit}><label className="grid gap-1.5 text-sm font-medium">Usuario<Input value={username} onChange={e => setUsername(e.target.value)} minLength={3} required /></label><label className="grid gap-1.5 text-sm font-medium">Contraseña<Input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></label><label className="grid gap-1.5 text-sm font-medium">Rol<Select value={role} onChange={e => setRole(e.target.value)}><option value="operator">Operador</option><option value="admin">Administrador</option></Select></label><Button>Crear usuario</Button>{message && <Alert variant="success" role="status">{message}</Alert>}</form></Card>
}
