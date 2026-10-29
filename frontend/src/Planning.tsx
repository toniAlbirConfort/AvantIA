import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { api } from './api'
import { issueText } from './SourceImports'
import type { PlanningBar, PlanningData } from './types'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'

const DAY_WIDTH = 44
function isoDate(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
function addDays(value: string, count: number) { const date = new Date(`${value}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10) }
function dayDiff(a: string, b: string) { return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000) }
function money(value: string) { return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(Number(value)) }
function dateLabel(value: string | null) { return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`)) : '—' }
const stateLabel: Record<string, string> = { confirmed: 'Confirmada', pre_reservation: 'Prereserva', paid: 'Pagada', unavailable: 'Bloqueado', stop_sell: 'StopSell', owner: 'De propietario', inquiry: 'Bajo petición', cancelled: 'Cancelada' }
const stateStyle: Record<string, string> = {
  confirmed: 'bg-plan-confirmada text-plan-confirmada-ink', paid: 'bg-plan-pagada text-plan-pagada-ink',
  pre_reservation: 'bg-plan-prereserva text-plan-prereserva-ink', owner: 'bg-plan-propietario text-plan-propietario-ink',
  unavailable: 'bg-plan-bloqueado text-plan-bloqueado-ink', stop_sell: 'bg-plan-stopsell text-plan-stopsell-ink',
  inquiry: 'bg-plan-peticion text-plan-peticion-ink',
  cancelled: 'bg-muted text-muted-foreground',
}
const legendItems = [
  { label: 'Prereserva', color: 'bg-plan-prereserva' },
  { label: 'Confirmada', color: 'bg-plan-confirmada' },
  { label: 'Pagada', color: 'bg-plan-pagada' },
  { label: 'De propietario', color: 'bg-plan-propietario' },
  { label: 'Bloqueado', color: 'bg-plan-bloqueado' },
  { label: 'StopSell', color: 'bg-plan-stopsell' },
  { label: 'Bajo petición', color: 'bg-plan-peticion' },
]

export function Planning() {
  const today = useMemo(() => isoDate(new Date()), [])
  const [start, setStart] = useState(today)
  const [end, setEnd] = useState(addDays(today, 60))
  const [search, setSearch] = useState('')
  const [data, setData] = useState<PlanningData | null>(null)
  const [selected, setSelected] = useState<PlanningBar | null>(null)
  const [error, setError] = useState('')
  const [legendOpen, setLegendOpen] = useState(() => window.matchMedia('(min-width: 768px)').matches)
  useEffect(() => {
    if (!start || !end || !Number.isFinite(dayDiff(start, end)) || dayDiff(start, end) < 1 || dayDiff(start, end) > 92) { setError('Selecciona un intervalo de 1 a 92 días.'); return }
    let cancelled = false
    const params = new URLSearchParams({ start, end })
    api<PlanningData>(`/api/planning?${params}`).then(result => { if (!cancelled) { setData(result); setError(''); setSelected(null) } }).catch(err => { if (!cancelled) setError((err as Error).message) })
    return () => { cancelled = true }
  }, [start, end])
  useEffect(() => {
    if (!selected) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    document.querySelector<HTMLButtonElement>('[data-slot="planning-dialog-close"]')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setSelected(null)
        return
      }
      if (event.key !== 'Tab') return
      const panel = document.querySelector<HTMLElement>('[data-slot="planning-dialog"][role="dialog"]')
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
  const count = Math.max(0, Math.min(92, dayDiff(start, end)))
  const days = Array.from({ length: count }, (_, index) => addDays(start, index))
  const groups: { label: string; days: number }[] = []
  for (const day of days) {
    const label = new Intl.DateTimeFormat('es-ES', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T00:00:00Z`))
    if (groups.at(-1)?.label === label) groups[groups.length - 1].days++
    else groups.push({ label, days: 1 })
  }
  const visible = data?.properties.filter(item => `${item.name} ${item.building || ''} ${item.id}`.toLocaleLowerCase('es-ES').includes(search.toLocaleLowerCase('es-ES'))) || []
  return <div className="grid gap-4">
    <Card className="flex flex-wrap items-end gap-4 p-4"><label className="grid min-w-40 flex-1 gap-1.5 text-sm font-medium">Desde<Input type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label className="grid min-w-40 flex-1 gap-1.5 text-sm font-medium">Hasta (exclusivo)<Input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></Card>
    <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">{data?.reservation_coverage ? `${data.reservations_in_range} en este intervalo · ${data.reservation_coverage.effective_rows} integradas` : 'Sin reservas importadas'}</span></div>
    {error && <Alert variant="destructive">{error}</Alert>}
    {!data?.property_batch || !data?.reservation_batch ? <Card className="p-6 text-sm leading-7 text-muted-foreground">Importa y confirma las exportaciones de propiedades y reservas para completar el planning. La falta de barras no confirma disponibilidad.</Card> : <>
      {data.reservation_coverage && <Alert variant="info">{data.reservation_coverage.effective_rows} reservas integradas · entradas desde {data.reservation_coverage.first_arrival} · salidas hasta {data.reservation_coverage.last_departure}. Se muestran las reservas del intervalo correspondientes a alojamientos activos.</Alert>}
      {data.reservations_in_range === 0 && <Alert variant="warning" role="status">No hay reservas de alojamientos activos entre {start} y {end}. La ausencia de barras no acredita que el alojamiento esté libre.</Alert>}
      <label className="grid max-w-md gap-1.5 text-sm font-medium">Buscar alojamiento<Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nombre, edificio o código" /></label>
      <div className="max-h-[68vh] max-w-full overflow-auto rounded-xl border border-border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" role="region" aria-label="Planning de alojamientos" tabIndex={0}>
        <div className="w-max min-w-full">
          <div className="sticky top-0 z-20 flex h-[4.75rem] w-max bg-card"><div className="sticky left-0 z-30 flex w-56 min-w-56 items-end border-r border-border bg-card p-3 text-xs font-semibold text-muted-foreground md:w-72 md:min-w-72">{visible.length} alojamientos</div><div className="flex flex-col" style={{ width: days.length * DAY_WIDTH }}><div className="flex h-9 border-b border-border">{groups.map((group, index) => <div className="shrink-0 border-r border-border px-2 py-2 text-xs font-semibold capitalize" key={index} style={{ width: group.days * DAY_WIDTH }}>{group.label}</div>)}</div><div className="flex h-10">{days.map(day => { const date = new Date(`${day}T00:00:00Z`); const weekend = date.getUTCDay() % 6 === 0; return <div key={day} className={`flex w-11 shrink-0 items-center justify-center gap-0.5 border-r border-border text-xs ${weekend ? 'bg-accent/60' : ''}`}>{date.getUTCDate()}<small className="text-[0.6rem] text-muted-foreground">{new Intl.DateTimeFormat('es-ES', { weekday: 'narrow', timeZone: 'UTC' }).format(date)}</small></div> })}</div></div></div>
          {visible.map((property, propertyIndex) => {
            const bars = (data.bars || []).filter(bar => bar.property_id === property.id).sort((a, b) => a.start_date.localeCompare(b.start_date))
            const laneEnds: string[] = []
            const placed = bars.map(bar => { let lane = laneEnds.findIndex(value => value <= bar.start_date); if (lane < 0) lane = laneEnds.length; laneEnds[lane] = bar.end_date; return { bar, lane } })
            const height = Math.max(46, laneEnds.length * 32 + 8)
            return <div className={`flex w-max border-b border-border ${propertyIndex % 2 ? 'bg-background' : 'bg-card'}`} key={property.id} style={{ height }}><div className={`sticky left-0 z-10 flex w-56 min-w-56 flex-col justify-center overflow-hidden border-r border-border px-3 py-2 md:w-72 md:min-w-72 ${propertyIndex % 2 ? 'bg-background' : 'bg-card'}`}><strong className="truncate text-xs">{property.name}</strong><small className="mt-1 truncate text-[0.65rem] text-muted-foreground">{property.building || property.id} · {property.category}{property.capacity > 0 ? ` · ${property.capacity} plazas` : ''}</small></div><div className="relative shrink-0" style={{ width: days.length * DAY_WIDTH, height }}><div aria-hidden="true" className="pointer-events-none absolute inset-0 flex">{days.map(day => <span key={day} className="h-full w-11 shrink-0 border-r border-border/70" />)}</div>{placed.map(({ bar, lane }) => {
              const left = Math.max(0, dayDiff(start, bar.start_date)) * DAY_WIDTH + 2
              const right = Math.min(count, dayDiff(start, bar.end_date)) * DAY_WIDTH - 2
              return <Button key={bar.locator} variant="ghost" className={`absolute z-10 h-[25px] min-h-0 justify-start overflow-hidden rounded px-2 py-0 text-left text-[0.68rem] font-semibold whitespace-nowrap hover:brightness-110 ${stateStyle[bar.status] || 'bg-secondary text-secondary-foreground'}`} style={{ left, width: Math.max(8, right - left), top: 7 + lane * 32 }} title={`${stateLabel[bar.status]} · ${bar.display_name} · ${bar.start_date} → ${bar.end_date}`} onClick={() => setSelected(bar)}>{bar.display_name} · {money(bar.total)}</Button>
            })}</div></div>
          })}
        </div>
      </div>
      {selected && <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/70 p-4" onClick={event => { if (event.target === event.currentTarget) setSelected(null) }}>
        <section data-slot="planning-dialog" className="my-auto max-h-[90vh] w-full max-w-3xl overflow-auto rounded-xl border border-border bg-card p-5 shadow-2xl sm:p-7" role="dialog" aria-modal="true" aria-labelledby="planning-detail-title" aria-describedby="planning-detail-description">
          <header className="mb-4 flex items-start justify-between gap-4 border-b border-border pb-4"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">RESERVA / {stateLabel[selected.status]?.toUpperCase()}</p><h2 id="planning-detail-title" className="text-xl font-semibold">{selected.display_name}</h2></div><Button type="button" variant="ghost" size="icon" data-slot="planning-dialog-close" className="shrink-0" onClick={() => setSelected(null)} aria-label="Cerrar detalle"><X aria-hidden="true" /></Button></header>
          <div><p id="planning-detail-description" className="mb-4 text-sm text-muted-foreground">Información operativa de la reserva.</p><dl className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3">{[['Estado', stateLabel[selected.status] || selected.status_original], ['Estado en exportación', selected.status_original], ['Localizador', selected.locator], ['Alojamiento', `${data.properties.find(item => item.id === selected.property_id)?.name || selected.property_id} · ${selected.property_id}`], ['Fecha de alta', dateLabel(selected.created_on)], ['Entrada', dateLabel(selected.start_date)], ['Salida', `${dateLabel(selected.end_date)} · exclusiva`], ['Noches', selected.nights], ['Adultos', selected.adults], ['Niños', selected.children], ['Bebés', selected.infants], ['Portal', selected.portal || '—']].map(([title, value]) => <div className="min-w-0" key={title}><dt className="text-xs text-muted-foreground">{title}</dt><dd className="mt-1 break-words text-sm">{value}</dd></div>)}</dl><h3 className="mb-3 mt-6 text-base font-semibold">Importes</h3><dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">{[['Total', money(selected.total)], ['Neto', money(selected.net)], ['Tasas', money(selected.tax)], ['Pagado', money(selected.paid)], ['Pendiente', money(selected.pending)]].map(([title, value]) => <div className="rounded-md bg-background p-3" key={title}><dt className="text-xs text-muted-foreground">{title}</dt><dd className="mt-1 text-sm font-semibold">{value}</dd></div>)}</dl></div>
        </section>
      </div>}
      <div className="grid gap-4 xl:grid-cols-2"><Card className="p-5"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">NO BLOQUEAN</p><h2 className="mb-4 text-lg font-semibold">Peticiones de información ({data.inquiries.length})</h2>{data.inquiries.length ? <ul className="mb-5 max-h-60 list-disc space-y-2 overflow-auto pl-5 text-sm">{data.inquiries.map(item => <li key={item.locator}><button type="button" className="text-left text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setSelected(item)} aria-label={`Abrir reserva ${item.locator}`}>{item.locator} · {item.property_id} · {item.start_date} → {item.end_date}</button></li>)}</ul> : <p className="mb-5 text-sm text-muted-foreground">Sin peticiones en el intervalo.</p>}<h2 className="mb-3 text-lg font-semibold">Canceladas ({data.cancellations.length})</h2>{data.cancellations.length ? <ul className="max-h-60 list-disc space-y-2 overflow-auto pl-5 text-sm">{data.cancellations.map(item => <li key={item.locator}><button type="button" className="text-left text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setSelected(item)} aria-label={`Abrir reserva ${item.locator}`}>{item.locator} · {item.property_id} · {item.start_date} → {item.end_date}</button></li>)}</ul> : <p className="text-sm text-muted-foreground">Sin cancelaciones en el intervalo.</p>}</Card><Card className="p-5"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">AUDITORÍA</p><h2 className="mb-4 text-lg font-semibold">Incidencias ({data.warnings.length})</h2>{data.warnings.length ? <ul className="max-h-72 list-disc space-y-2 overflow-auto pl-5 text-sm text-muted-foreground">{data.warnings.map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul> : <p className="text-sm text-muted-foreground">Sin incidencias registradas.</p>}</Card></div>
    </>}
    <aside className="fixed bottom-3 right-3 z-30 flex max-w-[calc(100vw-1.5rem)] items-center overflow-hidden rounded-lg border border-border bg-card shadow-md sm:bottom-5 sm:right-5" aria-label="Leyenda del planning">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-9 min-h-9 w-9 shrink-0 rounded-none border-r border-border p-0 text-muted-foreground"
        onClick={() => setLegendOpen(open => !open)}
        aria-expanded={legendOpen}
        aria-controls="planning-status-legend"
        aria-label={legendOpen ? 'Contraer leyenda de estados' : 'Desplegar leyenda de estados'}
      >
        {legendOpen ? <ChevronRight aria-hidden="true" /> : <ChevronLeft aria-hidden="true" />}
      </Button>
      <div id="planning-status-legend" className={legendOpen ? 'max-h-32 max-w-[calc(100vw-3.75rem)] overflow-y-auto p-2 sm:max-h-none sm:max-w-[min(70vw,56rem)] sm:overflow-visible' : 'px-3 py-2'}>
        {legendOpen
          ? <ul className="flex flex-wrap items-center gap-1.5 sm:flex-nowrap" aria-label="Estados de reserva">
            {legendItems.map(item => <li key={item.label} className="flex shrink-0 items-center gap-1.5 rounded bg-secondary px-2 py-1 text-xs text-secondary-foreground">
              <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${item.color}`} />{item.label}
            </li>)}
          </ul>
          : <div className="grid grid-cols-2 gap-1.5" aria-hidden="true">
            {legendItems.slice(0, 4).map(item => <span key={item.label} className={`size-2 rounded-full ${item.color}`} />)}
          </div>}
      </div>
    </aside>
  </div>
}
