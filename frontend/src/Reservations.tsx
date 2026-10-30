import { useEffect, useRef, useState } from 'react'
import { ExternalLink, RefreshCw, Search, SlidersHorizontal, X } from 'lucide-react'
import { api } from './api'
import type { ReservationRecord, ReservationsResponse } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

const PAGE_SIZE = 50
const statuses = [
  ['confirmed', 'Confirmada'],
  ['pre_reservation', 'Prereserva'],
  ['paid', 'Pagada'],
  ['inquiry', 'Petición de información'],
  ['cancelled', 'Cancelada'],
  ['unavailable', 'Bloqueado'],
  ['owner', 'De propietario'],
] as const

const statusAppearance: Record<string, { label: string; badge: string; stripe: string }> = {
  confirmed: { label: 'Confirmada', badge: 'bg-plan-confirmada text-plan-confirmada-ink', stripe: 'border-l-plan-confirmada' },
  pre_reservation: { label: 'Prereserva', badge: 'bg-plan-prereserva text-plan-prereserva-ink', stripe: 'border-l-plan-prereserva' },
  paid: { label: 'Pagada', badge: 'bg-plan-pagada text-plan-pagada-ink', stripe: 'border-l-plan-pagada' },
  inquiry: { label: 'Petición de información', badge: 'bg-plan-peticion text-plan-peticion-ink', stripe: 'border-l-plan-peticion' },
  cancelled: { label: 'Cancelada', badge: 'bg-muted text-muted-foreground', stripe: 'border-l-muted-foreground' },
  unavailable: { label: 'Bloqueado', badge: 'bg-plan-bloqueado text-plan-bloqueado-ink', stripe: 'border-l-plan-bloqueado' },
  stop_sell: { label: 'StopSell', badge: 'bg-plan-stopsell text-plan-stopsell-ink', stripe: 'border-l-plan-stopsell' },
  owner: { label: 'De propietario', badge: 'bg-plan-propietario text-plan-propietario-ink', stripe: 'border-l-plan-propietario' },
}

function dateLabel(value: string | null, options: Intl.DateTimeFormatOptions = { day: '2-digit', month: 'short', year: 'numeric' }) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('es-ES', { ...options, timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))
}

function money(value: string) {
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(Number(value || 0))
}

function occupancy(item: ReservationRecord) {
  const parts = [
    item.adults > 0 ? `${item.adults} ${item.adults === 1 ? 'adulto' : 'adultos'}` : '',
    item.children > 0 ? `${item.children} ${item.children === 1 ? 'niño' : 'niños'}` : '',
    item.infants > 0 ? `${item.infants} ${item.infants === 1 ? 'bebé' : 'bebés'}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'Ocupación no indicada'
}

function Status({ value }: { value: string }) {
  const appearance = statusAppearance[value] || { label: value, badge: 'bg-muted text-muted-foreground', stripe: 'border-l-muted-foreground' }
  return <Badge className={appearance.badge}><span className="mr-1.5 inline-block size-1.5 rounded-full bg-current" aria-hidden="true" />{appearance.label}</Badge>
}

export function Reservations({ onImport }: { onImport: () => void }) {
  const [data, setData] = useState<ReservationsResponse | null>(null)
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [propertyId, setPropertyId] = useState('')
  const [stayFrom, setStayFrom] = useState('')
  const [stayTo, setStayTo] = useState('')
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [selected, setSelected] = useState<ReservationRecord | null>(null)
  const requestVersion = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  function parameters(offset: number) {
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE), sort_order: sortOrder })
    if (query) params.set('q', query)
    if (status) params.set('status', status)
    if (propertyId) params.set('property_id', propertyId)
    if (stayFrom) params.set('stay_from', stayFrom)
    if (stayTo) params.set('stay_to', stayTo)
    return params
  }

  useEffect(() => {
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    setData(null)
    api<ReservationsResponse>(`/api/reservations?${parameters(0)}`)
      .then(result => { if (version === requestVersion.current) setData(result) })
      .catch(err => { if (version === requestVersion.current) setError((err as Error).message) })
      .finally(() => { if (version === requestVersion.current) setLoading(false) })
    return () => { requestVersion.current += 1 }
  }, [query, status, propertyId, stayFrom, stayTo, sortOrder, retryKey])

  async function loadMore() {
    if (!data || loadingMore || data.items.length >= data.total) return
    const version = requestVersion.current
    setLoadingMore(true)
    try {
      const next = await api<ReservationsResponse>(`/api/reservations?${parameters(data.items.length)}`)
      if (version === requestVersion.current) {
        setData(current => current ? { ...next, items: [...current.items, ...next.items] } : next)
      }
    } catch (err) {
      if (version === requestVersion.current) setError((err as Error).message)
    } finally {
      if (version === requestVersion.current) setLoadingMore(false)
    }
  }

  useEffect(() => {
    if (!selected) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.querySelector<HTMLButtonElement>('[data-slot="reservation-dialog-close"]')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setSelected(null)
        return
      }
      if (event.key !== 'Tab') return
      const panel = document.querySelector<HTMLElement>('[data-slot="reservation-dialog"][role="dialog"]')
      const focusable = panel?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previousFocus?.focus()
    }
  }, [selected])

  const activeFilters = [status, propertyId, stayFrom, stayTo].filter(Boolean).length
  const clearFilters = () => { setStatus(''); setPropertyId(''); setStayFrom(''); setStayTo('') }
  const detailProperty = selected && data?.properties.find(property => property.id === selected.property_id)?.name

  return <div className="grid gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">Reservas</h1>
        <p className="mt-1 text-sm text-muted-foreground">Reservas sincronizadas desde Avantio, ordenadas por fecha de creación.</p>
      </div>
      <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <label className="relative min-w-56 flex-1 sm:w-80 sm:flex-none">
          <Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input className="pl-9" type="search" aria-label="Buscar por localizador, ID o huésped" placeholder="Localizador, ID o huésped…" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        <Button type="button" variant="outline" className="gap-2" aria-expanded={filtersOpen} aria-controls="reservation-filters" onClick={() => setFiltersOpen(open => !open)}>
          <SlidersHorizontal size={16} /> Filtros
          {activeFilters > 0 && <span className="grid size-5 place-items-center rounded-full bg-primary text-[0.65rem] text-primary-foreground">{activeFilters}</span>}
        </Button>
        <Button type="button" variant="ghost" size="icon" onClick={() => setRetryKey(value => value + 1)} disabled={loading} aria-label="Actualizar reservas"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} /></Button>
      </div>
    </div>

    {filtersOpen && <Card id="reservation-filters" className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-5">
      <label className="grid gap-1.5 text-xs font-medium">Estado<Select value={status} onChange={event => setStatus(event.target.value)}><option value="">Todos los estados</option>{statuses.map(([value, caption]) => <option key={value} value={value}>{caption}</option>)}</Select></label>
      <label className="grid gap-1.5 text-xs font-medium">Alojamiento<Select value={propertyId} onChange={event => setPropertyId(event.target.value)}><option value="">Todos los alojamientos</option>{data?.properties.map(property => <option key={property.id} value={property.id}>{property.name} · {property.id}</option>)}</Select></label>
      <label className="grid gap-1.5 text-xs font-medium">Estancia desde<Input type="date" value={stayFrom} onChange={event => setStayFrom(event.target.value)} /></label>
      <label className="grid gap-1.5 text-xs font-medium">Estancia hasta<Input type="date" value={stayTo} onChange={event => setStayTo(event.target.value)} /></label>
      <Button type="button" variant="ghost" className="self-end" onClick={clearFilters} disabled={activeFilters === 0}><X size={15} /> Limpiar filtros</Button>
    </Card>}

    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <p className="text-muted-foreground" aria-live="polite">{loading ? 'Cargando reservas…' : `${data?.total ?? 0} ${data?.total === 1 ? 'reserva' : 'reservas'}`}</p>
      <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">Fecha de creación
        <Select className="h-9 w-44" value={sortOrder} onChange={event => setSortOrder(event.target.value as 'desc' | 'asc')} aria-label="Ordenar reservas por fecha de creación">
          <option value="desc">Más reciente primero</option><option value="asc">Más antigua primero</option>
        </Select>
      </label>
    </div>

    {error && <Alert variant="destructive" className="flex items-center justify-between gap-4"><span>{error}</span><Button type="button" variant="outline" size="sm" onClick={() => setRetryKey(value => value + 1)}>Reintentar</Button></Alert>}

    {loading && !data && <div className="grid gap-2" aria-label="Cargando reservas" role="status">{Array.from({ length: 5 }, (_, index) => <Skeleton className="h-20 w-full rounded-md" key={index} />)}</div>}

    {!loading && !error && data && data.items.length === 0 && <Card className="grid justify-items-center gap-3 px-5 py-12 text-center">
      <h2 className="text-lg font-semibold">{data.has_snapshot ? 'No hay reservas con estos filtros' : 'Aún no hay reservas importadas'}</h2>
      <p className="max-w-lg text-sm leading-6 text-muted-foreground">{data.has_snapshot ? 'Prueba a cambiar la búsqueda o limpiar los filtros.' : 'Cuando se sincronice o importes el listado de reservas de Avantio, aparecerá aquí.'}</p>
      {!data.has_snapshot && <Button type="button" variant="outline" onClick={onImport}>Ir a Importaciones</Button>}
    </Card>}

    {data && data.items.length > 0 && <>
      <div className="hidden overflow-x-auto rounded-lg border border-border bg-card lg:block">
        <Table className="min-w-[1050px]">
          <TableHeader><TableRow className="hover:bg-transparent"><TableHead>Reserva</TableHead><TableHead>Estado</TableHead><TableHead>Localizador</TableHead><TableHead>Cliente</TableHead><TableHead>Alertas</TableHead><TableHead className="text-right">Importe</TableHead><TableHead><span className="sr-only">Detalle</span></TableHead></TableRow></TableHeader>
          <TableBody>{data.items.map(item => {
            const state = statusAppearance[item.status] || statusAppearance.cancelled
            return <TableRow key={item.locator} className={`border-l-4 ${state.stripe} bg-brand-yellow-light/35 hover:bg-brand-yellow-light/55`}>
              <TableCell className="min-w-60"><strong className="block text-sm">{dateLabel(item.start_date)} – {dateLabel(item.end_date)}</strong><span className="mt-1 block max-w-64 truncate text-xs text-muted-foreground" title={item.property_name}>{item.property_name} · {item.property_id}</span></TableCell>
              <TableCell><Status value={item.status} /></TableCell>
              <TableCell><strong className="font-medium">{item.locator}</strong><small className="mt-1 block text-xs text-muted-foreground">Creada {dateLabel(item.created_on)}</small></TableCell>
              <TableCell className="min-w-56"><span className="block max-w-64 truncate font-medium" title={item.display_name}>{item.display_name || 'Huésped sin nombre'}</span><small className="mt-1 block text-xs text-muted-foreground">{occupancy(item)}</small></TableCell>
              <TableCell>{Number(item.pending) > 0 ? <span className="inline-flex rounded-md bg-warning-bg px-2 py-1 text-xs font-medium text-warning-ink">Pendiente {money(item.pending)}</span> : <span className="text-xs text-muted-foreground">Sin pendiente</span>}</TableCell>
              <TableCell className="text-right font-semibold tabular-nums">{money(item.total)}</TableCell>
              <TableCell className="w-12"><Button type="button" variant="ghost" size="icon" aria-label={`Ver reserva ${item.locator}`} onClick={() => setSelected(item)}><ExternalLink size={16} /></Button></TableCell>
            </TableRow>
          })}</TableBody>
        </Table>
      </div>

      <div className="grid gap-3 lg:hidden">
        {data.items.map(item => {
          const state = statusAppearance[item.status] || statusAppearance.cancelled
          return <Card className={`overflow-hidden border-l-4 ${state.stripe} bg-brand-yellow-light/35 p-4`} key={item.locator}>
            <div className="flex items-start justify-between gap-3"><div className="min-w-0"><strong className="block text-sm">{dateLabel(item.start_date)} – {dateLabel(item.end_date)}</strong><span className="mt-1 block truncate text-xs text-muted-foreground">{item.property_name} · {item.property_id}</span></div><Status value={item.status} /></div>
            <div className="mt-3 grid grid-cols-[1fr_auto] items-end gap-3"><div className="min-w-0"><strong className="block truncate text-sm">{item.display_name || 'Huésped sin nombre'}</strong><small className="mt-1 block text-xs text-muted-foreground">{item.locator} · {occupancy(item)}</small></div><strong className="text-right text-sm tabular-nums">{money(item.total)}</strong></div>
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-border/70 pt-3"><small className="text-xs text-muted-foreground">Creada {dateLabel(item.created_on)}</small>{Number(item.pending) > 0 && <span className="rounded-md bg-warning-bg px-2 py-1 text-xs font-medium text-warning-ink">Pendiente {money(item.pending)}</span>}</div>
            <Button type="button" variant="outline" size="sm" className="mt-3 w-full" onClick={() => setSelected(item)}>Ver detalle <ExternalLink size={14} /></Button>
          </Card>
        })}
      </div>

      {data.items.length < data.total && <Button type="button" variant="outline" className="mx-auto min-w-48" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Cargando…' : `Mostrar más (${data.items.length} de ${data.total})`}</Button>}
    </>}

    {selected && <div className="fixed inset-0 z-50 flex justify-end bg-black/45" onClick={event => { if (event.target === event.currentTarget) setSelected(null) }}>
      <section data-slot="reservation-dialog" className="flex h-full max-h-[100dvh] w-full max-w-xl flex-col overflow-hidden border-l border-border bg-card shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="reservation-detail-title" aria-describedby="reservation-detail-description">
        <header className="flex items-start justify-between gap-4 border-b border-border p-5 sm:p-7"><div className="min-w-0"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-primary">RESERVA / {statusAppearance[selected.status]?.label.toUpperCase() || selected.status.toUpperCase()}</p><h2 id="reservation-detail-title" className="break-words text-xl font-semibold">{selected.display_name || 'Huésped sin nombre'}</h2><p id="reservation-detail-description" className="mt-1 text-sm text-muted-foreground">Localizador {selected.locator}</p></div><Button type="button" variant="ghost" size="icon" data-slot="reservation-dialog-close" className="shrink-0" onClick={() => setSelected(null)} aria-label="Cerrar detalle"><X aria-hidden="true" /></Button></header>
        <div className="flex-1 overflow-y-auto p-5 sm:p-7">
          <div className="mb-5 flex flex-wrap items-center gap-2"><Status value={selected.status} /><span className="text-xs text-muted-foreground">Creada {dateLabel(selected.created_on)}</span></div>
          <dl className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3">
            {[
              ['Alojamiento', `${detailProperty || selected.property_name} · ${selected.property_id}`],
              ['Entrada', dateLabel(selected.start_date, { dateStyle: 'long' })],
              ['Salida', dateLabel(selected.end_date, { dateStyle: 'long' })],
              ['Noches', selected.nights], ['Ocupación', occupancy(selected)], ['Portal', selected.portal || '—'],
            ].map(([title, value]) => <div className="min-w-0" key={title}><dt className="text-xs text-muted-foreground">{title}</dt><dd className="mt-1 break-words text-sm">{value}</dd></div>)}
          </dl>
          <h3 className="mb-3 mt-7 text-base font-semibold">Importes</h3>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {[
              ['Total', selected.total], ['Neto', selected.net], ['Tasas', selected.tax], ['Pagado', selected.paid], ['Pendiente', selected.pending],
            ].map(([title, value]) => <div className="rounded-md bg-background p-3" key={title}><dt className="text-xs text-muted-foreground">{title}</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{money(String(value))}</dd></div>)}
          </dl>
          {Number(selected.pending) > 0 && <Alert variant="info" className="mt-5">Importe pendiente de {money(selected.pending)}. Esta vista es solo de consulta.</Alert>}
        </div>
      </section>
    </div>}
  </div>
}
