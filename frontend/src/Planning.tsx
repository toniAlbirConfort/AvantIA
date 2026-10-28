import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api } from './api'
import { issueText } from './SourceImports'
import type { PlanningBar, PlanningData } from './types'
import { Button } from './components/ui/button'

const DAY_WIDTH = 44
function isoDate(date: Date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` }
function addDays(value: string, count: number) { const date = new Date(`${value}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10) }
function dayDiff(a: string, b: string) { return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000) }
function money(value: string) { return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(Number(value)) }
function dateLabel(value: string | null) { return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`)) : '—' }
const stateLabel: Record<string, string> = { confirmed: 'Confirmada', pre_reservation: 'Prerreserva', paid: 'Pagada', unavailable: 'No disponible', owner: 'De propietario', inquiry: 'Petición de información', cancelled: 'Cancelada' }

export function Planning() {
  const today = useMemo(() => isoDate(new Date()), [])
  const [start, setStart] = useState(today)
  const [end, setEnd] = useState(addDays(today, 60))
  const [search, setSearch] = useState('')
  const [data, setData] = useState<PlanningData | null>(null)
  const [selected, setSelected] = useState<PlanningBar | null>(null)
  const [error, setError] = useState('')
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
    document.querySelector<HTMLButtonElement>('.planning-close')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setSelected(null)
        return
      }
      if (event.key !== 'Tab') return
      const panel = document.querySelector<HTMLElement>('.planning-detail[role="dialog"]')
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
  return <div className="planning-page">
    <div className="planning-toolbar card"><label>Desde<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label>Hasta (exclusivo)<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></div>
    <div className="planning-meta"><span className="planning-key confirmed">Confirmada · bloquea</span><span className="planning-key paid">Pagada · bloquea</span><span className="planning-key pre_reservation">Prerreserva · bloquea</span><span className="planning-key owner">Propietario · bloquea</span><span className="planning-key unavailable">No disponible · bloquea</span><span className="planning-key inquiry">Peticiones · lista inferior</span><span className="planning-key cancelled">Canceladas · lista inferior</span><span>{data?.reservation_coverage ? `${data.reservations_in_range} en este intervalo · ${data.reservation_coverage.effective_rows} integradas` : 'Sin reservas importadas'}</span></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data?.property_batch || !data?.reservation_batch ? <div className="card empty">Importa y confirma las exportaciones de propiedades y reservas para completar el planning. La falta de barras no confirma disponibilidad.</div> : <>
      {data.reservation_coverage && <p className="planning-coverage">{data.reservation_coverage.effective_rows} reservas integradas · entradas desde {data.reservation_coverage.first_arrival} · salidas hasta {data.reservation_coverage.last_departure}. Se muestran las reservas del intervalo correspondientes a alojamientos activos.</p>}
      {data.reservations_in_range === 0 && <div className="card empty" role="status">No hay reservas de alojamientos activos entre {start} y {end}. La ausencia de barras no acredita que el alojamiento esté libre.</div>}
      <label className="planning-search">Buscar alojamiento<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Nombre, edificio o código" /></label>
      <div className="planning-scroll card" role="region" aria-label="Planning de alojamientos" tabIndex={0}>
        <div className="planning-grid" style={{ width: `calc(var(--planning-label-width) + ${days.length * DAY_WIDTH}px)` }}>
          <div className="planning-header"><div className="planning-sticky planning-corner">{visible.length} alojamientos</div><div className="planning-dates" style={{ width: days.length * DAY_WIDTH }}><div className="planning-months">{groups.map((group, index) => <div key={index} style={{ width: group.days * DAY_WIDTH }}>{group.label}</div>)}</div><div className="planning-days">{days.map(day => <div key={day} className={new Date(`${day}T00:00:00Z`).getUTCDay() % 6 === 0 ? 'weekend' : ''}>{new Date(`${day}T00:00:00Z`).getUTCDate()}<small>{new Intl.DateTimeFormat('es-ES', { weekday: 'narrow', timeZone: 'UTC' }).format(new Date(`${day}T00:00:00Z`))}</small></div>)}</div></div></div>
          {visible.map(property => {
            const bars = (data.bars || []).filter(bar => bar.property_id === property.id).sort((a, b) => a.start_date.localeCompare(b.start_date))
            const laneEnds: string[] = []
            const placed = bars.map(bar => { let lane = laneEnds.findIndex(value => value <= bar.start_date); if (lane < 0) lane = laneEnds.length; laneEnds[lane] = bar.end_date; return { bar, lane } })
            const height = Math.max(46, laneEnds.length * 32 + 8)
            return <div className="planning-row" key={property.id} style={{ height }}><div className="planning-sticky planning-property"><strong>{property.name}</strong><small>{property.building || property.id} · {property.category}{property.capacity > 0 ? ` · ${property.capacity} plazas` : ''}</small></div><div className="planning-track" style={{ width: days.length * DAY_WIDTH, height, backgroundSize: `${DAY_WIDTH}px 100%` }}>{placed.map(({ bar, lane }) => {
              const left = Math.max(0, dayDiff(start, bar.start_date)) * DAY_WIDTH + 2
              const right = Math.min(count, dayDiff(start, bar.end_date)) * DAY_WIDTH - 2
              return <Button key={bar.locator} className={`planning-bar ${bar.status}`} style={{ left, width: Math.max(8, right - left), top: 7 + lane * 32 }} title={`${stateLabel[bar.status]} · ${bar.display_name} · ${bar.start_date} → ${bar.end_date}`} onClick={() => setSelected(bar)}>{bar.display_name} · {money(bar.total)}</Button>
            })}</div></div>
          })}
        </div>
      </div>
      {selected && <div className="planning-overlay" onClick={event => { if (event.target === event.currentTarget) setSelected(null) }}>
        <section className="card planning-detail" role="dialog" aria-modal="true" aria-labelledby="planning-detail-title" aria-describedby="planning-detail-description">
          <header className="planning-detail-header"><div><p className="eyebrow">RESERVA / {stateLabel[selected.status]?.toUpperCase()}</p><h2 id="planning-detail-title">{selected.display_name}</h2></div><Button type="button" variant="ghost" size="icon" className="planning-close" onClick={() => setSelected(null)} aria-label="Cerrar detalle"><X aria-hidden="true" /></Button></header>
          <div className="planning-detail-content"><p id="planning-detail-description" className="planning-detail-lead">Información operativa de la reserva.</p><dl>
            <div><dt>Estado</dt><dd>{stateLabel[selected.status] || selected.status_original}</dd></div>
            <div><dt>Estado en exportación</dt><dd>{selected.status_original}</dd></div>
            <div><dt>Localizador</dt><dd>{selected.locator}</dd></div>
            <div><dt>Alojamiento</dt><dd>{data.properties.find(item => item.id === selected.property_id)?.name || selected.property_id} · {selected.property_id}</dd></div>
            <div><dt>Fecha de alta</dt><dd>{dateLabel(selected.created_on)}</dd></div>
            <div><dt>Entrada</dt><dd>{dateLabel(selected.start_date)}</dd></div>
            <div><dt>Salida</dt><dd>{dateLabel(selected.end_date)} · exclusiva</dd></div>
            <div><dt>Noches</dt><dd>{selected.nights}</dd></div>
            <div><dt>Adultos</dt><dd>{selected.adults}</dd></div>
            <div><dt>Niños</dt><dd>{selected.children}</dd></div>
            <div><dt>Bebés</dt><dd>{selected.infants}</dd></div>
            <div><dt>Portal</dt><dd>{selected.portal || '—'}</dd></div>
          </dl><h3>Importes</h3><dl className="planning-amounts">
            <div><dt>Total</dt><dd>{money(selected.total)}</dd></div>
            <div><dt>Neto</dt><dd>{money(selected.net)}</dd></div>
            <div><dt>Tasas</dt><dd>{money(selected.tax)}</dd></div>
            <div><dt>Pagado</dt><dd>{money(selected.paid)}</dd></div>
            <div><dt>Pendiente</dt><dd>{money(selected.pending)}</dd></div>
          </dl></div>
        </section>
      </div>}
      <div className="planning-lower"><section className="card"><p className="eyebrow">NO BLOQUEAN</p><h2>Peticiones de información ({data.inquiries.length})</h2>{data.inquiries.length ? <ul>{data.inquiries.map(item => <li key={item.locator}><button type="button" className="planning-reservation-link" onClick={() => setSelected(item)} aria-label={`Abrir reserva ${item.locator}`}>{item.locator} · {item.property_id} · {item.start_date} → {item.end_date}</button></li>)}</ul> : <p>Sin peticiones en el intervalo.</p>}<h2>Canceladas ({data.cancellations.length})</h2>{data.cancellations.length ? <ul>{data.cancellations.map(item => <li key={item.locator}><button type="button" className="planning-reservation-link" onClick={() => setSelected(item)} aria-label={`Abrir reserva ${item.locator}`}>{item.locator} · {item.property_id} · {item.start_date} → {item.end_date}</button></li>)}</ul> : <p>Sin cancelaciones en el intervalo.</p>}</section><section className="card"><p className="eyebrow">AUDITORÍA</p><h2>Incidencias ({data.warnings.length})</h2>{data.warnings.length ? <ul>{data.warnings.map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul> : <p>Sin incidencias registradas.</p>}</section></div>
    </>}
  </div>
}
