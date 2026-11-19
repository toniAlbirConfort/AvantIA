import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, RefreshCw, Search } from 'lucide-react'
import { api, apiDownload } from './api'
import { CustomerPanel } from './Customers'
import type { CustomerSummary, RetentionGuest, RetentionMatch, RetentionResponse } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'

const PAGE_SIZE = 50
const YEARS_BACK = 6
const euros = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })
const integer = new Intl.NumberFormat('es-ES')
const matchLabels: Record<RetentionMatch, { label: string; tone: string; title: string }> = {
  exact: { label: 'Ficha única', tone: 'bg-success-bg text-success-ink', title: 'El nombre coincide con una sola ficha de cliente' },
  ambiguous: { label: 'Varias fichas', tone: 'bg-warning-bg text-warning-ink', title: 'Varias fichas de cliente tienen este nombre: confirma a quién contactar' },
  none: { label: 'Sin ficha', tone: 'bg-muted text-muted-foreground', title: 'Ninguna ficha de cliente tiene exactamente este nombre' },
}
type Contact = 'any' | 'email' | 'phone' | 'opt_in'

function shortDate(value: string) {
  return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))
}

function Metric({ label, value, detail }: { label: string; value: number | undefined; detail: string }) {
  return <Card className="p-4">
    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
    <strong className="mt-1 block text-3xl font-semibold tabular-nums">{value === undefined ? '—' : integer.format(value)}</strong>
    <small className="text-xs text-muted-foreground">{detail}</small>
  </Card>
}

export function CustomerRetention({ onImport }: { onImport: () => void }) {
  const currentYear = new Date().getFullYear()
  const yearOptions = Array.from({ length: YEARS_BACK }, (_, index) => currentYear - YEARS_BACK + index)
  const [baseYears, setBaseYears] = useState<number[]>([currentYear - 3, currentYear - 2, currentYear - 1])
  // Clicking several years in a row sends one request, once the selection settles.
  const [requestedYears, setRequestedYears] = useState<number[]>(baseYears)
  const [minStays, setMinStays] = useState('1')
  const [channel, setChannel] = useState('')
  const [contact, setContact] = useState<Contact>('any')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [data, setData] = useState<RetentionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [selected, setSelected] = useState<CustomerSummary | null>(null)
  const requestVersion = useRef(0)
  const closePanel = useCallback(() => setSelected(null), [])

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    const timer = window.setTimeout(() => setRequestedYears(baseYears), 400)
    return () => window.clearTimeout(timer)
  }, [baseYears])

  const parameters = useCallback((offset: number) => {
    const params = new URLSearchParams({ target_year: String(currentYear), min_stays: minStays, contact, offset: String(offset), limit: String(PAGE_SIZE) })
    requestedYears.forEach(year => params.append('base_years', String(year)))
    if (channel) params.set('channel', channel)
    if (query) params.set('q', query)
    return params
  }, [currentYear, requestedYears, minStays, channel, contact, query])

  useEffect(() => {
    if (!requestedYears.length) return
    const version = ++requestVersion.current
    setLoading(true); setError('')
    api<RetentionResponse>(`/api/customers/retention?${parameters(0)}`)
      .then(result => { if (version === requestVersion.current) setData(result) })
      .catch(err => { if (version === requestVersion.current) setError((err as Error).message) })
      .finally(() => { if (version === requestVersion.current) setLoading(false) })
    return () => { requestVersion.current += 1 }
  }, [parameters, requestedYears.length, retryKey])

  async function loadMore() {
    if (!data || loadingMore || data.items.length >= data.total) return
    const version = requestVersion.current
    setLoadingMore(true)
    try {
      const next = await api<RetentionResponse>(`/api/customers/retention?${parameters(data.items.length)}`)
      if (version === requestVersion.current) setData(current => current ? { ...next, items: [...current.items, ...next.items] } : next)
    } catch (err) {
      if (version === requestVersion.current) setError((err as Error).message)
    } finally {
      if (version === requestVersion.current) setLoadingMore(false)
    }
  }

  async function exportCsv() {
    setExporting(true); setError('')
    const params = parameters(0)
    params.delete('offset'); params.delete('limit')
    try { await apiDownload(`/api/customers/retention.csv?${params}`, `clientes-a-recuperar-${currentYear}.csv`) }
    catch (err) { setError((err as Error).message) }
    finally { setExporting(false) }
  }

  function toggleYear(year: number) {
    setBaseYears(current => current.includes(year) ? current.filter(item => item !== year) : [...current, year].sort())
  }

  function openCustomer(item: RetentionGuest) {
    if (item.match !== 'exact' || !item.avantio_id) return
    setSelected({ avantio_id: item.avantio_id, display_name: item.display_name, email: item.email, phone: item.phone, country: item.country, document_number: null, customer_class: null })
  }

  const updating = loading || requestedYears.join() !== baseYears.join() || search.trim() !== query
  const yearsLabel = baseYears.length ? baseYears.join(', ') : 'ningún año'
  const filtersChanged = minStays !== '1' || !!channel || contact !== 'any' || !!search

  return <div className="grid gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">Clientes a recuperar</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Huéspedes con estancias confirmadas o pagadas en {yearsLabel} que no tienen ninguna reserva activa para {currentYear} ni hecha en {currentYear}.</p>
      </div>
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" onClick={() => void exportCsv()} disabled={exporting || !data?.total}><Download size={16} aria-hidden="true" /> {exporting ? 'Exportando…' : 'Exportar CSV'}</Button>
        <Button type="button" variant="ghost" size="icon" onClick={() => setRetryKey(value => value + 1)} disabled={loading} aria-label="Actualizar clientes a recuperar"><RefreshCw size={16} className={loading ? 'animate-spin motion-reduce:animate-none' : ''} /></Button>
      </div>
    </div>

    <Card className="grid gap-4 p-4 sm:p-5">
      <fieldset className="grid gap-2">
        <legend className="mb-2 text-xs font-medium">Años de referencia</legend>
        <div className="flex flex-wrap gap-2">{yearOptions.map(year => <Button key={year} type="button" size="sm" variant={baseYears.includes(year) ? 'secondary' : 'outline'} aria-pressed={baseYears.includes(year)} onClick={() => toggleYear(year)}>{year}</Button>)}</div>
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(14rem,2fr)_repeat(3,minmax(10rem,1fr))_auto] xl:items-end">
        <label className="relative grid gap-1 text-xs font-medium">Buscar
          <span className="relative"><Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" type="search" placeholder="Nombre del huésped" value={search} onChange={event => setSearch(event.target.value)} /></span>
        </label>
        <label className="grid gap-1 text-xs font-medium">Estancias mínimas<Select value={minStays} onChange={event => setMinStays(event.target.value)}><option value="1">1 o más</option><option value="2">2 o más</option><option value="3">3 o más</option></Select></label>
        <label className="grid gap-1 text-xs font-medium">Canal habitual<Select value={channel} onChange={event => setChannel(event.target.value)}><option value="">Todos</option>{(data?.channels || []).map(item => <option key={item} value={item}>{item}</option>)}</Select></label>
        <label className="grid gap-1 text-xs font-medium">Contacto<Select value={contact} onChange={event => setContact(event.target.value as Contact)}><option value="any">Todos</option><option value="email">Con email</option><option value="phone">Con teléfono</option><option value="opt_in">Acepta campañas email</option></Select></label>
        {filtersChanged && <Button type="button" variant="ghost" onClick={() => { setMinStays('1'); setChannel(''); setContact('any'); setSearch('') }}>Limpiar</Button>}
      </div>
    </Card>

    {!baseYears.length && <Alert variant="info" role="status">Elige al menos un año de referencia.</Alert>}
    {error && <Alert variant="destructive" className="flex items-center justify-between gap-4"><span>{error}</span><Button type="button" variant="outline" size="sm" onClick={() => setRetryKey(value => value + 1)}>Reintentar</Button></Alert>}

    {data && data.has_snapshot && baseYears.length > 0 && updating && <p role="status" className="-mb-2 flex items-center gap-2 text-sm font-medium text-info-ink"><RefreshCw size={15} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />Actualizando resultados…</p>}

    {data && data.has_snapshot && baseYears.length > 0 && <div aria-busy={updating} className={`grid grid-cols-2 gap-3 transition-opacity motion-reduce:transition-none xl:grid-cols-4 ${updating ? 'opacity-50' : ''}`}>
      <Metric label="Huéspedes" value={data.summary.guests} detail={`Sin reserva en ${currentYear}`} />
      <Metric label="Con ficha única" value={data.summary.linked} detail="Enlazados a un cliente" />
      <Metric label="Con email" value={data.summary.with_email} detail="Se les puede escribir" />
      <Metric label="Aceptan campañas" value={data.summary.opt_in} detail="Opt-in de newsletters" />
    </div>}

    {loading && !data && <div className="grid gap-2" role="status" aria-label="Cargando clientes a recuperar">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-20 w-full rounded-md" />)}</div>}

    {data && !data.has_snapshot && <Card className="grid justify-items-center gap-3 px-5 py-12 text-center">
      <h2 className="text-lg font-semibold">Aún no hay reservas importadas</h2>
      <p className="max-w-lg text-sm leading-6 text-muted-foreground">Esta lista se calcula con el listado de reservas de Avantio y se enlaza con el listado de clientes.</p>
      <Button type="button" variant="outline" onClick={onImport}>Ir a Importaciones</Button>
    </Card>}

    {data && data.has_snapshot && baseYears.length > 0 && <>
      <div className="hidden grid-cols-[minmax(12rem,2fr)_minmax(8rem,1fr)_minmax(10rem,1.4fr)_minmax(7rem,0.8fr)_minmax(12rem,1.6fr)] gap-4 px-5 text-sm text-muted-foreground xl:grid" aria-hidden="true">
        <span>Huésped</span><span>Historial</span><span>Última estancia</span><span>Canal</span><span>Contacto</span>
      </div>
      {data.items.length === 0 && !loading && <Card className="px-5 py-10 text-center text-sm text-muted-foreground">Ningún huésped cumple estos filtros.</Card>}
      <ul aria-busy={updating} className={`grid gap-2 transition-opacity motion-reduce:transition-none ${updating ? 'opacity-50' : ''}`} aria-label="Clientes a recuperar">
        {data.items.map(item => {
          const match = matchLabels[item.match]
          const linked = item.match === 'exact' && !!item.avantio_id
          const content = <>
            <span className="grid min-w-0 gap-1.5">
              <strong className="truncate text-sm font-semibold" title={item.display_name}>{item.display_name}</strong>
              <span className="flex flex-wrap gap-1">{item.years.map(year => <Badge key={year} className="bg-muted font-medium text-muted-foreground">{year}</Badge>)}</span>
            </span>
            <span className="text-sm"><span className="text-muted-foreground xl:hidden">Historial: </span><span className="tabular-nums">{item.stays} {item.stays === 1 ? 'estancia' : 'estancias'} · {item.nights} noches</span><small className="block text-xs text-muted-foreground tabular-nums">{euros.format(Number(item.total))}</small></span>
            <span className="min-w-0 text-sm"><span className="text-muted-foreground xl:hidden">Última estancia: </span>{shortDate(item.last_stay_start)}<small className="block truncate text-xs text-muted-foreground" title={item.property_name || undefined}>{item.property_name || '—'} · {item.last_locator}</small></span>
            <span className="text-sm"><span className="text-muted-foreground xl:hidden">Canal: </span>{item.channel || '—'}</span>
            <span className="grid min-w-0 gap-1 text-sm">
              <span><Badge className={match.tone} title={match.title}>{match.label}{item.match === 'ambiguous' ? ` (${item.candidates})` : ''}</Badge>{item.email_opt_in && <Badge className="ml-1 bg-info-bg text-info-ink">Campañas</Badge>}</span>
              {linked && <span className="truncate text-muted-foreground" title={item.email || undefined}>{item.email || item.phone || 'Sin email ni teléfono'}</span>}
            </span>
          </>
          const layout = 'grid w-full gap-2 rounded-md border border-border border-l-4 bg-card px-5 py-4 text-left xl:grid-cols-[minmax(12rem,2fr)_minmax(8rem,1fr)_minmax(10rem,1.4fr)_minmax(7rem,0.8fr)_minmax(12rem,1.6fr)] xl:items-center xl:gap-4'
          return <li key={item.key}>
            {linked
              ? <button type="button" className={`${layout} border-l-border transition-colors hover:border-l-brand-orange hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none`} onClick={() => openCustomer(item)} aria-label={`Abrir ficha de ${item.display_name}`}>{content}</button>
              : <div className={`${layout} border-l-border`}>{content}</div>}
          </li>
        })}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground" aria-live="polite">Resultados {data.items.length.toLocaleString('es-ES')} de {data.total.toLocaleString('es-ES')}</p>
        {data.items.length < data.total && <Button type="button" variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Cargando…' : 'Mostrar más'}</Button>}
      </div>
      <p className="text-xs leading-5 text-muted-foreground">El listado de reservas de Avantio no incluye el Id de cliente, así que las estancias se agrupan por nombre del huésped y se enlazan con la ficha que tiene exactamente ese nombre. Revisa las marcadas como «Varias fichas» o «Sin ficha» antes de contactar. La exportación queda registrada.</p>
    </>}

    {selected && <CustomerPanel summary={selected} onClose={closePanel} />}
  </div>
}
