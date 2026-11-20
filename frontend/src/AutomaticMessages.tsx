import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { CircleAlert, CircleCheck, CircleHelp, EyeOff, Mail, MailCheck, MailQuestion, MailX, MessageCircle, Search, type LucideIcon } from 'lucide-react'
import { api } from './api'
import type { AutomaticMessage, AutomaticMessageChannel, AutomaticMessageList, AutomaticMessageStatus, StaffReply } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'

const PAGE_SIZE = 50
type Period = 'today' | '7d' | '30d' | '90d' | 'all'
type StaffFilter = '' | 'after' | 'none'

const statuses: Record<AutomaticMessageStatus, { label: string; summary: string; icon: LucideIcon; tone: string }> = {
  sent: { label: 'Enviado', summary: 'Enviados', icon: CircleCheck, tone: 'bg-success-bg text-success-ink' },
  failed: { label: 'Fallido', summary: 'Fallidos', icon: CircleAlert, tone: 'bg-error-bg text-error-ink' },
  delivery_unknown: { label: 'Envío incierto', summary: 'Inciertos', icon: CircleHelp, tone: 'bg-warning-bg text-warning-ink' },
  shadow: { label: 'No enviado (modo sombra)', summary: 'Modo sombra', icon: EyeOff, tone: 'bg-info-bg text-info-ink' },
}
const statusHelp: Record<AutomaticMessageStatus, string> = {
  sent: 'El servidor de correo aceptó el mensaje.',
  failed: 'El envío falló y no se reintenta.',
  delivery_unknown: 'El envío empezó pero no terminó: comprueba en el buzón si llegó a salir. No se reintenta.',
  shadow: 'Se decidió enviarlo, pero el modo sombra solo lo registra.',
}
const channels: Record<AutomaticMessageChannel, { label: string; icon: LucideIcon }> = {
  email: { label: 'Email', icon: Mail },
  whatsapp: { label: 'WhatsApp', icon: MessageCircle },
}
const languages: Record<string, string> = { es: 'Español', en: 'Inglés', fr: 'Francés', de: 'Alemán', nl: 'Neerlandés', it: 'Italiano', pt: 'Portugués' }

function dateTime(value: string) {
  return new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}
function periodStart(period: Period) {
  if (period === 'all') return ''
  const today = new Date()
  const back = period === 'today' ? 0 : period === '7d' ? 6 : period === '30d' ? 29 : 89
  return new Date(today.getFullYear(), today.getMonth(), today.getDate() - back).toISOString()
}

function elapsed(hours: number) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min después`
  if (hours < 48) return `${Math.round(hours)} h después`
  return `${Math.round(hours / 24)} días después`
}

// The last email the team sent to the recipient from Outlook, read from "Elementos enviados".
function StaffReplyLine({ value }: { value: StaffReply | null }) {
  let Icon = MailQuestion
  let tone = 'text-muted-foreground'
  let text: React.ReactNode = 'Pendiente de comprobar en Elementos enviados.'
  if (value && value.after_auto_reply && value.last_sent_at) {
    Icon = MailCheck
    tone = 'text-success-ink'
    text = <><strong className="font-semibold">Último email del equipo:</strong> {dateTime(value.last_sent_at)} · {elapsed(value.hours_after || 0)} · {value.in_thread ? 'en este hilo' : 'en otro hilo'}{value.subject && <> · <span className="break-words">{value.subject}</span></>}</>
  } else if (value && value.last_sent_at) {
    Icon = MailX
    tone = 'text-warning-ink'
    text = <><strong className="font-semibold">Sin email del equipo después de la autorespuesta.</strong> El último es del {dateTime(value.last_sent_at)}{value.subject && <> · <span className="break-words">{value.subject}</span></>}</>
  } else if (value) {
    Icon = MailX
    tone = 'text-warning-ink'
    text = <strong className="font-semibold">El equipo no le ha escrito nunca desde el buzón.</strong>
  }
  return <p className={`flex items-start gap-2 rounded-lg border border-border bg-background px-3 py-2 text-xs leading-5 ${tone}`}>
    <Icon size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
    <span className="min-w-0">{text}</span>
  </p>
}

function MessageRow({ item, onOpenConversation }: { item: AutomaticMessage; onOpenConversation: (link: string) => void }) {
  const [open, setOpen] = useState(false)
  const status = statuses[item.status]
  const channel = channels[item.channel] || channels.email
  const StatusIcon = status.icon
  const ChannelIcon = channel.icon
  const textId = `automatic-message-${item.id.replace(':', '-')}`
  return <li>
    <Card className="grid gap-3 p-4 sm:p-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <span className={`hidden size-9 shrink-0 place-items-center rounded-full sm:grid ${status.tone}`}><StatusIcon size={18} aria-hidden="true" /></span>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <Badge className={status.tone}>{status.label}</Badge>
            <Badge className="bg-muted text-muted-foreground"><ChannelIcon size={12} aria-hidden="true" /> {channel.label}</Badge>
            <span className="text-xs text-muted-foreground">{item.kind_label}</span>
            {item.copy_failed && <Badge className="bg-warning-bg text-warning-ink">Sin copia en Enviados</Badge>}
          </div>
          <h2 className="truncate text-base font-semibold leading-6">{item.customer_name ? `${item.customer_name} · ` : ''}<span className="font-normal text-muted-foreground">{item.recipient}</span></h2>
          {item.subject && <p className="truncate text-sm text-foreground">{item.subject}</p>}
          <p className="mt-1 text-xs text-muted-foreground">{dateTime(item.created_at)}{item.language ? ` · ${languages[item.language] || item.language.toUpperCase()}` : ''} · {statusHelp[item.status]}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col sm:items-stretch">
          <Button type="button" size="sm" variant="outline" aria-expanded={open} aria-controls={textId} onClick={() => setOpen(value => !value)}>{open ? 'Ocultar mensaje' : 'Ver mensaje'}</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => onOpenConversation(`/${item.conversation_kind === 'booking' ? 'bookings' : 'inbox'}/${item.conversation_id}`)}>Abrir conversación</Button>
        </div>
      </div>
      <StaffReplyLine value={item.staff_reply} />
      {item.error && <Alert variant="destructive"><span className="break-words">{item.error}</span></Alert>}
      {item.copy_failed && <p className="text-xs text-warning-ink">El mensaje salió, pero no se pudo guardar la copia en la carpeta de enviados del buzón.</p>}
      {open && <p id={textId} className="whitespace-pre-wrap break-words rounded-lg border border-border bg-background p-3 text-sm leading-6 text-muted-foreground">{item.text}</p>}
    </Card>
  </li>
}

export function AutomaticMessages({ refreshVersion, onOpenConversation }: { refreshVersion: number; onOpenConversation: (link: string) => void }) {
  const [channel, setChannel] = useState<AutomaticMessageChannel | ''>('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState<AutomaticMessageStatus | ''>('')
  const [period, setPeriod] = useState<Period>('30d')
  const [staff, setStaff] = useState<StaffFilter>('')
  const [searchText, setSearchText] = useState('')
  const [query, setQuery] = useState('')
  const [data, setData] = useState<AutomaticMessageList | null>(null)
  const [error, setError] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)

  const params = useCallback((offset: number) => {
    const search = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
    if (channel) search.set('channel', channel)
    if (kind) search.set('kind', kind)
    if (status) search.set('status', status)
    if (query) search.set('q', query)
    if (staff) search.set('staff_reply', staff)
    const from = periodStart(period)
    if (from) search.set('from', from)
    return search
  }, [channel, kind, status, query, period, staff])

  // Automatic refreshes keep the pages already shown instead of collapsing to the first one.
  const shown = useRef(PAGE_SIZE)
  const load = useCallback(async () => {
    try {
      const search = params(0)
      search.set('limit', String(Math.min(Math.max(shown.current, PAGE_SIZE), 200)))
      setData(await api<AutomaticMessageList>(`/api/automatic-messages?${search}`))
      setError('')
    } catch (err) { setError((err as Error).message) }
  }, [params])

  useEffect(() => { shown.current = PAGE_SIZE; setData(null); void load() }, [load, refreshVersion])
  useEffect(() => { const timer = window.setInterval(() => void load(), 30000); return () => clearInterval(timer) }, [load])

  async function loadMore() {
    if (!data) return
    setLoadingMore(true)
    try {
      const next = await api<AutomaticMessageList>(`/api/automatic-messages?${params(data.items.length)}`)
      const items = [...data.items, ...next.items]
      shown.current = items.length
      setData({ ...next, items })
    } catch (err) { setError((err as Error).message) } finally { setLoadingMore(false) }
  }
  function submitSearch(event: FormEvent) { event.preventDefault(); setQuery(searchText.trim()) }
  function clearFilters() { setChannel(''); setKind(''); setStatus(''); setPeriod('30d'); setStaff(''); setSearchText(''); setQuery('') }

  const counts = data?.counts
  const totalInPeriod = counts ? Object.values(counts).reduce((sum, value) => sum + value, 0) : 0
  const filtered = Boolean(channel || kind || status || staff || query || period !== '30d')

  return <div className="grid gap-5">
    <section aria-label="Resumen de envíos automáticos" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {(Object.keys(statuses) as AutomaticMessageStatus[]).map(key => {
        const meta = statuses[key]
        const Icon = meta.icon
        const active = status === key
        return <button key={key} type="button" aria-pressed={active} onClick={() => setStatus(active ? '' : key)} className={`flex min-h-20 items-center gap-3 rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${active ? 'border-primary' : 'border-border'}`}>
          <span className={`grid size-9 shrink-0 place-items-center rounded-full ${meta.tone}`}><Icon size={18} aria-hidden="true" /></span>
          <span className="grid min-w-0">
            {counts ? <strong className="text-2xl font-semibold leading-tight">{counts[key]}</strong> : <Skeleton className="h-7 w-10" />}
            <span className="text-xs text-muted-foreground">{meta.summary}</span>
          </span>
        </button>
      })}
    </section>

    <Card className="grid gap-4 p-5">
      <form className="flex gap-2" role="search" onSubmit={submitSearch}>
        <label className="sr-only" htmlFor="automatic-messages-search">Buscar envíos automáticos</label>
        <Input id="automatic-messages-search" value={searchText} onChange={event => setSearchText(event.target.value)} placeholder="Destinatario, nombre o asunto" />
        <Button type="submit" variant="outline"><Search size={16} aria-hidden="true" /> Buscar</Button>
      </form>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <label className="grid gap-1 text-xs font-semibold text-muted-foreground">Periodo
          <Select value={period} onChange={event => setPeriod(event.target.value as Period)}>
            <option value="today">Hoy</option>
            <option value="7d">Últimos 7 días</option>
            <option value="30d">Últimos 30 días</option>
            <option value="90d">Últimos 90 días</option>
            <option value="all">Todo el historial</option>
          </Select>
        </label>
        <label className="grid gap-1 text-xs font-semibold text-muted-foreground">Canal
          <Select value={channel} onChange={event => setChannel(event.target.value as AutomaticMessageChannel | '')}>
            <option value="">Todos</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        </label>
        <label className="grid gap-1 text-xs font-semibold text-muted-foreground">Tipo de envío
          <Select value={kind} onChange={event => setKind(event.target.value)}>
            <option value="">Todos</option>
            {data?.sources.map(source => <option key={source.kind} value={source.kind}>{source.label}</option>)}
          </Select>
        </label>
        <label className="grid gap-1 text-xs font-semibold text-muted-foreground">Estado
          <Select value={status} onChange={event => setStatus(event.target.value as AutomaticMessageStatus | '')}>
            <option value="">Todos</option>
            {(Object.keys(statuses) as AutomaticMessageStatus[]).map(key => <option key={key} value={key}>{statuses[key].label}</option>)}
          </Select>
        </label>
        <label className="grid gap-1 text-xs font-semibold text-muted-foreground">Email del equipo después
          <Select value={staff} onChange={event => setStaff(event.target.value as StaffFilter)}>
            <option value="">Todos</option>
            <option value="after">Con email posterior</option>
            <option value="none">Sin email posterior</option>
          </Select>
        </label>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <p aria-live="polite">{data ? `${data.total} de ${totalInPeriod} envío${totalInPeriod === 1 ? '' : 's'} automático${totalInPeriod === 1 ? '' : 's'} con estos filtros.` : 'Cargando…'}</p>
        {filtered && <Button type="button" size="sm" variant="ghost" onClick={clearFilters}>Quitar filtros</Button>}
      </div>
    </Card>

    {error && <Alert variant="destructive">{error}</Alert>}

    {!data
      ? <div className="grid gap-3">{[0, 1, 2].map(key => <Skeleton key={key} className="h-28 w-full rounded-xl" />)}</div>
      : !data.items.length
        ? <Card className="grid min-h-40 place-items-center gap-2 p-8 text-center">
          <CircleCheck className="text-muted-foreground" aria-hidden="true" />
          <p className="max-w-md text-sm leading-6 text-muted-foreground">
            {channel === 'whatsapp'
              ? 'AvantIA todavía no envía WhatsApp automáticos. Cuando lo haga, aparecerán aquí.'
              : filtered ? 'Ningún envío automático coincide con estos filtros.' : 'No se ha enviado nada de forma automática en los últimos 30 días.'}
          </p>
        </Card>
        : <>
          <ul className="grid gap-3" aria-label="Envíos automáticos">
            {data.items.map(item => <MessageRow key={item.id} item={item} onOpenConversation={onOpenConversation} />)}
          </ul>
          {data.items.length < data.total && <div className="flex justify-center"><Button type="button" variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? 'Cargando…' : `Cargar más (${data.total - data.items.length} restantes)`}</Button></div>}
        </>}
  </div>
}
