import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { BookOpen, ChevronDown, ChevronRight, Mail, Phone, RefreshCw, Search, X } from 'lucide-react'
import { api } from './api'
import { Status } from './Reservations'
import type { CustomerDetail, CustomerSummary, CustomersResponse, ReservationRecord } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Skeleton } from './components/ui/skeleton'

const PAGE_SIZE = 40
const avatarTones = [
  'bg-brand-orange-light text-brand-orange-ink',
  'bg-brand-blue-light text-brand-blue-ink',
  'bg-brand-turquoise-light text-brand-turquoise-ink',
  'bg-brand-yellow-light text-brand-yellow-ink',
]
const classTones: Record<string, string> = {
  Problemático: 'bg-warning-bg text-warning-ink',
  Potencial: 'bg-info-bg text-info-ink',
}
// Fields Avantio shows in the customer panel that its customer export does not include.
const PENDING = Symbol('pending')
type FieldValue = ReactNode | typeof PENDING

function dateLabel(value: string | null) {
  if (!value) return null
  const [year, month, day] = value.slice(0, 10).split('-')
  return `${day}/${month}/${year}`
}

function stayLabel(item: ReservationRecord) {
  const today = new Date().toISOString().slice(0, 10)
  if (item.end_date < today) return 'Pasada'
  if (item.start_date > today) return 'Próxima'
  return 'En curso'
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))
}

function Avatar({ customer, large = false }: { customer: CustomerSummary; large?: boolean }) {
  const tone = avatarTones[Number(customer.avantio_id) % avatarTones.length] || avatarTones[0]
  const initial = customer.display_name.match(/[\p{L}\p{N}]/u)?.[0].toUpperCase() || '?'
  return <span aria-hidden="true" className={`grid shrink-0 place-items-center rounded-md font-semibold ${tone} ${large ? 'size-20 text-3xl' : 'size-10 text-lg'}`}>{initial}</span>
}

function CustomerClass({ value }: { value: string | null }) {
  if (!value) return <span className="text-sm text-muted-foreground">—</span>
  return <Badge className={classTones[value] || 'bg-muted text-muted-foreground'}>{value}</Badge>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <details className="group rounded-lg border border-border bg-card">
    <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-4 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
      {title}<ChevronDown size={16} aria-hidden="true" className="text-muted-foreground transition-transform group-open:rotate-180 motion-reduce:transition-none" />
    </summary>
    <dl className="border-t border-border px-4">{children}</dl>
  </details>
}

function Field({ label, value }: { label: string; value: FieldValue }) {
  const empty = value === null || value === undefined || value === ''
  return <div className="flex items-start justify-between gap-4 border-b border-border/70 py-2.5 last:border-b-0">
    <dt className="text-sm font-medium">{label}</dt>
    <dd className="min-w-0 break-words text-right text-sm text-muted-foreground">
      {value === PENDING
        ? <Badge className="bg-muted font-medium text-muted-foreground" title="Avantio muestra este dato, pero no viene en la exportación de clientes">Por trackear</Badge>
        : empty ? '—' : value as ReactNode}
    </dd>
  </div>
}

function ContactLink({ href, children, className = 'text-foreground hover:text-primary' }: { href: string; children: ReactNode; className?: string }) {
  return <a className={`break-all underline underline-offset-2 ${className}`} href={href}>{children}</a>
}

function CustomerPanel({ summary, onClose }: { summary: CustomerSummary; onClose: () => void }) {
  const [detail, setDetail] = useState<CustomerDetail | null>(null)
  const [error, setError] = useState('')
  const [showReservations, setShowReservations] = useState(false)

  useEffect(() => {
    let active = true
    setDetail(null); setError(''); setShowReservations(false)
    api<CustomerDetail>(`/api/customers/${encodeURIComponent(summary.avantio_id)}`)
      .then(result => { if (active) setDetail(result) })
      .catch(err => { if (active) setError((err as Error).message) })
    return () => { active = false }
  }, [summary.avantio_id])

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.querySelector<HTMLButtonElement>('[data-slot="customer-dialog-close"]')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab') return
      const panel = document.querySelector<HTMLElement>('[data-slot="customer-dialog"]')
      const focusable = panel?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], summary, [tabindex]:not([tabindex="-1"])')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); previousFocus?.focus() }
  }, [onClose])

  const customer = detail || summary
  const reservations = detail?.reservations || []
  const contactLine = detail && [
    detail.email && <ContactLink key="email" className="text-white hover:text-white/80" href={`mailto:${detail.email}`}>{detail.email}</ContactLink>,
    detail.phone && <ContactLink key="phone" className="text-white hover:text-white/80" href={`tel:${detail.phone.replace(/\s+/g, '')}`}>{detail.phone}</ContactLink>,
    detail.language && <span key="language">{detail.language}</span>,
  ].filter(Boolean)

  return <div className="fixed inset-0 z-50 flex justify-end bg-black/45" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <section data-slot="customer-dialog" className="flex h-full max-h-[100dvh] w-full max-w-xl flex-col overflow-hidden border-l border-border bg-background shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="customer-detail-title">
      <header className="flex items-center justify-between gap-4 border-b border-border px-5 py-4 sm:px-7">
        <h2 className="text-xl font-semibold">Cliente</h2>
        <Button type="button" variant="ghost" size="icon" data-slot="customer-dialog-close" onClick={onClose} aria-label="Cerrar ficha de cliente"><X aria-hidden="true" /></Button>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-6 sm:px-7">
        <div className="grid justify-items-center gap-2 rounded-lg bg-brand-charcoal px-5 pb-5 pt-6 text-center text-white">
          <Avatar customer={customer} large />
          <h3 id="customer-detail-title" className="mt-2 break-words text-base font-semibold uppercase tracking-wide">{customer.display_name}</h3>
          {contactLine && contactLine.length > 0 && <p className="flex flex-wrap justify-center gap-x-2 gap-y-1 text-sm text-white/85">{contactLine.map((item, index) => <span key={index} className="flex gap-2">{index > 0 && <span aria-hidden="true">|</span>}{item}</span>)}</p>}
          <div className="mt-1 flex gap-2">
            {detail?.email && <Button asChild variant="ghost" size="icon" className="rounded-full border border-white/50 text-white hover:bg-white/15 hover:text-white"><a href={`mailto:${detail.email}`} aria-label={`Escribir a ${detail.email}`}><Mail size={18} /></a></Button>}
            {detail?.phone && <Button asChild variant="ghost" size="icon" className="rounded-full border border-white/50 text-white hover:bg-white/15 hover:text-white"><a href={`tel:${detail.phone.replace(/\s+/g, '')}`} aria-label={`Llamar al ${detail.phone}`}><Phone size={18} /></a></Button>}
          </div>
        </div>

        {error && <Alert variant="destructive" className="mt-5">{error}</Alert>}
        {!detail && !error && <div className="mt-5 grid gap-3" role="status" aria-label="Cargando ficha">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-12 w-full rounded-lg" />)}</div>}

        {detail && <div className="mt-6 grid gap-3">
          <div className="mb-1"><p className="text-sm font-semibold">Observaciones</p><p className="mt-1 whitespace-pre-line break-words text-sm text-muted-foreground">{detail.notes || 'Sin observaciones.'}</p></div>

          <div className="rounded-lg border border-border bg-card">
            <button type="button" className="flex min-h-16 w-full items-center gap-3 rounded-lg px-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-expanded={showReservations} aria-controls="customer-reservations" onClick={() => setShowReservations(open => !open)}>
              <BookOpen size={20} aria-hidden="true" />
              <span className="flex-1"><strong className="block text-sm">Reservas</strong><small className="text-xs text-muted-foreground">{reservations.length} {reservations.length === 1 ? 'reserva' : 'reservas'}</small></span>
              <ChevronRight size={18} aria-hidden="true" className={`transition-transform motion-reduce:transition-none ${showReservations ? 'rotate-90' : ''}`} />
            </button>
            {showReservations && <div id="customer-reservations" className="border-t border-border px-4 py-3">
              {reservations.length ? <ul className="grid gap-3">{reservations.map(item => <li key={item.locator} className="flex items-center justify-between gap-3">
                <span className="min-w-0"><span className="block truncate text-sm" title={item.property_name}>{item.property_name} ({item.locator})</span><small className="text-xs text-muted-foreground">{shortDate(item.start_date)} – {shortDate(item.end_date)} ({stayLabel(item)})</small></span>
                <Status value={item.status} />
              </li>)}</ul> : <p className="text-sm text-muted-foreground">No hay reservas importadas con este nombre de huésped.</p>}
              <p className="mt-3 text-xs text-muted-foreground">Vinculadas por nombre del huésped: el listado de reservas no incluye el Id de cliente.</p>
            </div>}
          </div>

          <Section title="Datos generales">
            <Field label="Id cliente Avantio" value={detail.avantio_id} />
            <Field label="Fecha de alta" value={dateLabel(detail.registered_on)} />
            <Field label="Origen marketing" value={PENDING} />
            <Field label="Tratamiento" value={PENDING} />
            <Field label="Moneda cliente" value={PENDING} />
            <Field label="Clase" value={detail.customer_class} />
            <Field label="Tipo cliente" value={detail.customer_type} />
            <Field label="Idioma" value={detail.language} />
            <Field label="Periodo de estancia" value={detail.stay_period} />
          </Section>
          <Section title="Contacto">
            <Field label="E-mail" value={detail.email && <ContactLink href={`mailto:${detail.email}`}>{detail.email}</ContactLink>} />
            <Field label="E-mail alternativo" value={detail.email_alt && <ContactLink href={`mailto:${detail.email_alt}`}>{detail.email_alt}</ContactLink>} />
            <Field label="Teléfono" value={detail.phone} />
            <Field label="Teléfono alternativo 1" value={detail.phone_alt1} />
            <Field label="Teléfono alternativo 2" value={detail.phone_alt2} />
            <Field label="Recibida autorización para enviar campañas email (opt-in)" value={detail.email_opt_in === null ? null : detail.email_opt_in ? 'Sí' : 'No'} />
          </Section>
          <Section title="Domicilio">
            <Field label="Calle" value={[detail.street, detail.street_number, detail.door].filter(Boolean).join(', ')} />
            <Field label="Código postal" value={detail.postal_code} />
            <Field label="País" value={detail.country} />
            <Field label="Región" value={detail.region} />
            <Field label="Provincia" value={detail.province} />
            <Field label="Ciudad" value={detail.city} />
          </Section>
          <Section title="Documentación">
            <Field label="Tipo documento" value={PENDING} />
            <Field label="Número documento" value={detail.document_number} />
            <Field label="Expedido fecha" value={PENDING} />
            <Field label="Fecha de validez" value={PENDING} />
            <Field label="Sexo" value={detail.sex} />
            <Field label="Nacionalidad (País)" value={detail.nationality} />
            <Field label="Fecha nacimiento" value={dateLabel(detail.birth_date)} />
          </Section>
          <Section title="Datos contables">
            <Field label="Cuenta bancaria" value={detail.bank_account_masked} />
            <Field label="Código fiscal" value={detail.tax_code} />
            <Field label="Cuenta contable" value={detail.accounting_account} />
            <Field label="Retención IRPF" value={PENDING} />
            <Field label="Tipo de facturación" value={PENDING} />
            <Field label="Oposición a mostrar la dirección en factura" value={PENDING} />
          </Section>
          <p className="mt-2 text-xs text-muted-foreground">Origen: {detail.source.filename || 'importación'} · fila {detail.source_row}{detail.source.imported_at ? ` · ${new Date(detail.source.imported_at).toLocaleString('es-ES')}` : ''}. «Por trackear» marca datos que Avantio muestra pero que su exportación de clientes no incluye.</p>
        </div>}
      </div>
    </section>
  </div>
}

export function Customers({ onImport }: { onImport: () => void }) {
  const [data, setData] = useState<CustomersResponse | null>(null)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [selected, setSelected] = useState<CustomerSummary | null>(null)
  const requestVersion = useRef(0)
  const closePanel = useCallback(() => setSelected(null), [])

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  function parameters(offset: number) {
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) })
    if (query) params.set('q', query)
    return params
  }

  useEffect(() => {
    const version = ++requestVersion.current
    setLoading(true); setError('')
    api<CustomersResponse>(`/api/customers?${parameters(0)}`)
      .then(result => { if (version === requestVersion.current) setData(result) })
      .catch(err => { if (version === requestVersion.current) setError((err as Error).message) })
      .finally(() => { if (version === requestVersion.current) setLoading(false) })
    return () => { requestVersion.current += 1 }
  }, [query, retryKey])

  async function loadMore() {
    if (!data || loadingMore || data.items.length >= data.total) return
    const version = requestVersion.current
    setLoadingMore(true)
    try {
      const next = await api<CustomersResponse>(`/api/customers?${parameters(data.items.length)}`)
      if (version === requestVersion.current) setData(current => current ? { ...next, items: [...current.items, ...next.items] } : next)
    } catch (err) {
      if (version === requestVersion.current) setError((err as Error).message)
    } finally {
      if (version === requestVersion.current) setLoadingMore(false)
    }
  }

  return <div className="grid gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">Clientes</h1>
        <p className="mt-1 text-sm text-muted-foreground">Fichas importadas del listado de clientes de Avantio.</p>
      </div>
      <div className="flex w-full items-center gap-2 sm:w-auto">
        <label className="relative min-w-56 flex-1 sm:w-96 sm:flex-none">
          <Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-9" type="search" aria-label="Buscar clientes" placeholder="Buscar por nombre, email, teléfono o documento" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        <Button type="button" variant="ghost" size="icon" onClick={() => setRetryKey(value => value + 1)} disabled={loading} aria-label="Actualizar clientes"><RefreshCw size={16} className={loading ? 'animate-spin motion-reduce:animate-none' : ''} /></Button>
      </div>
    </div>

    {error && <Alert variant="destructive" className="flex items-center justify-between gap-4"><span>{error}</span><Button type="button" variant="outline" size="sm" onClick={() => setRetryKey(value => value + 1)}>Reintentar</Button></Alert>}

    {loading && !data && <div className="grid gap-2" role="status" aria-label="Cargando clientes">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-20 w-full rounded-md" />)}</div>}

    {data && !data.has_data && <Card className="grid justify-items-center gap-3 px-5 py-12 text-center">
      <h2 className="text-lg font-semibold">Aún no hay clientes importados</h2>
      <p className="max-w-lg text-sm leading-6 text-muted-foreground">Exporta el listado de clientes desde Avantio e impórtalo como conjunto «Clientes».</p>
      <Button type="button" variant="outline" onClick={onImport}>Ir a Importaciones</Button>
    </Card>}

    {data && data.has_data && <>
      <div className="hidden grid-cols-[minmax(16rem,2.2fr)_minmax(14rem,2fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(7rem,0.8fr)] gap-4 px-5 text-sm text-muted-foreground lg:grid" aria-hidden="true">
        <span className="pl-14">Nombre</span><span>Contacto</span><span>País</span><span>DNI/ID</span><span>Clase</span>
      </div>
      {data.items.length === 0 && !loading && <Card className="px-5 py-10 text-center text-sm text-muted-foreground">Ningún cliente coincide con «{query}».</Card>}
      <ul className={`grid gap-2 transition-opacity motion-reduce:transition-none ${loading ? 'opacity-60' : ''}`} aria-label="Clientes">
        {data.items.map(item => <li key={item.avantio_id}>
          <button type="button" className="grid w-full gap-2 rounded-md border border-border border-l-4 border-l-border bg-card px-5 py-4 text-left transition-colors hover:border-l-brand-orange hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none lg:grid-cols-[minmax(16rem,2.2fr)_minmax(14rem,2fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(7rem,0.8fr)] lg:items-center lg:gap-4" onClick={() => setSelected(item)}>
            <span className="flex min-w-0 items-center gap-4"><Avatar customer={item} /><strong className="min-w-0 truncate text-sm font-semibold" title={item.display_name}>{item.display_name}</strong></span>
            <span className="grid min-w-0 gap-1 pl-14 text-sm text-muted-foreground lg:pl-0"><span className="truncate" title={item.email || undefined}>{item.email || 'Sin email'}</span><span className="truncate">{item.phone || 'Sin teléfono'}</span></span>
            <span className="pl-14 text-sm lg:pl-0"><span className="text-muted-foreground lg:hidden">País: </span>{item.country || '—'}</span>
            <span className="truncate pl-14 text-sm lg:pl-0"><span className="text-muted-foreground lg:hidden">DNI/ID: </span>{item.document_number || '—'}</span>
            <span className="pl-14 lg:pl-0"><CustomerClass value={item.customer_class} /></span>
          </button>
        </li>)}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">Resultados {data.items.length.toLocaleString('es-ES')} de {data.total.toLocaleString('es-ES')}</p>
        {data.items.length < data.total && <Button type="button" variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Cargando…' : 'Mostrar más'}</Button>}
      </div>
    </>}

    {selected && <CustomerPanel summary={selected} onClose={closePanel} />}
  </div>
}
