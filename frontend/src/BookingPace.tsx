import { useEffect, useState } from 'react'
import { api } from './api'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Card } from './components/ui/card'
import { Select } from './components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

type Offer = { discount: number; label: string; rate?: string }
type Week = {
  week: number; start: string; end: string; occupancy: number | null; average: number | null; drop: number | null
  offer: Offer | null; real_price?: string | null; sa_next?: string | null; projected?: string | null
}
type Month = { month: number; booked: number; booked_previous: number; final_previous: number; change: number | null; status: 'behind' | 'slightly_behind' | 'on_track' | 'little_reference' }
type GroupPace = {
  units: number; weeks: Week[]; pace: Month[]
  annual?: Record<string, string>; growth_4y?: number | null; yearly_change?: Record<string, number | null>
  sa_next_vs_real?: number | null; sa_next_average?: string | null; projected_average?: string | null
}
type PaceView = {
  as_of: string; year: number; next_year: number; comparison_years: number[]; threshold: number
  offer_rules: { min_drop: number; discount: number; label: string }[]
  projection: { id: number; name: string; target_year: number } | null
  groups: Record<string, GroupPace>
}

const ALL = 'Todos'
const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
const statusLabels: Record<Month['status'], [string, string]> = {
  behind: ['Por detrás', 'bg-error-bg text-error-ink'],
  slightly_behind: ['Algo por detrás', 'bg-warning-bg text-warning-ink'],
  on_track: ['En línea o por delante', 'bg-success-bg text-success-ink'],
  little_reference: ['Poca referencia', 'bg-secondary text-secondary-foreground'],
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function price(value: string | null | undefined) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(Number(value))
}

function percent(value: number | null | undefined, signed = false, digits = 0) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: digits, signDisplay: signed ? 'exceptZero' : 'auto' }).format(value)
}

function points(value: number) {
  return `−${Math.round(value * 100)} pts`
}

function offerRanges(view: PaceView) {
  const rules = view.offer_rules.slice().sort((a, b) => a.min_drop - b.min_drop)
  return rules.map((rule, index) => {
    const low = Math.round(Math.max(rule.min_drop, view.threshold) * 100)
    const next = rules[index + 1]
    const range = next ? `${low}–${Math.round(next.min_drop * 100)} puntos` : `${low} puntos o más`
    return `${range} → −${Math.round(rule.discount * 100)} %`
  }).filter((_, index) => !rules[index + 1] || rules[index + 1].min_drop > view.threshold).join('; ')
}

function PaceChart({ months, year, nextYear, asOf }: { months: Month[]; year: number; nextYear: number; asOf: string }) {
  const width = 900, height = 220, left = 46, right = 10, top = 12, bottom = 26
  const max = Math.max(10, ...months.flatMap(item => [item.booked, item.booked_previous]))
  const step = max > 600 ? 200 : max > 300 ? 100 : max > 120 ? 50 : 20
  const ceiling = Math.ceil(max / step) * step
  const y = (value: number) => top + (1 - value / ceiling) * (height - top - bottom)
  const band = (width - left - right) / 12
  const bar = Math.min(22, band / 2 - 4)
  const ticks = Array.from({ length: ceiling / step + 1 }, (_, index) => index * step)
  return <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={`Noches reservadas por mes: ${nextYear} a ${formatDate(asOf)} frente a ${year} en la misma fecha del año anterior`}>
    {ticks.map(value => <g key={value}>
      <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="stroke-border" />
      <text x={left - 6} y={y(value)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">{value}</text>
    </g>)}
    {months.map((item, index) => {
      const center = left + band * index + band / 2
      const heightNow = y(0) - y(item.booked), heightBefore = y(0) - y(item.booked_previous)
      return <g key={item.month}>
        <title>{`${MONTHS[index]}: ${nextYear} ${item.booked} noches · ${year} a la misma fecha ${item.booked_previous} · ${year} final ${item.final_previous}`}</title>
        <rect x={center - bar - 1} y={y(item.booked)} width={bar} height={heightNow} rx={Math.min(4, heightNow / 2)} className="fill-primary" />
        <rect x={center + 1} y={y(item.booked_previous)} width={bar} height={heightBefore} rx={Math.min(4, heightBefore / 2)} className="fill-muted-foreground opacity-50" />
        <rect x={center - bar - 2} y={top} width={bar * 2 + 4} height={height - top - bottom} fill="transparent" />
        <text x={center} y={height - 8} textAnchor="middle" className="fill-muted-foreground text-[11px]">{MONTHS[index]}</text>
      </g>
    })}
  </svg>
}

function WeeksChart({ weeks, year, years }: { weeks: Week[]; year: number; years: number[] }) {
  const width = 900, height = 230, left = 40, right = 10, top = 12, bottom = 26
  const x = (week: number) => left + ((week - 1) / 51) * (width - left - right)
  const y = (value: number) => top + (1 - Math.min(value, 1)) * (height - top - bottom)
  const path = (field: 'occupancy' | 'average') => {
    let d = '', pen = false
    for (const item of weeks) {
      const value = item[field]
      if (value == null) { pen = false; continue }
      d += `${pen ? 'L' : 'M'}${x(item.week).toFixed(1)},${y(value).toFixed(1)}`
      pen = true
    }
    return d
  }
  return <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={`Ocupación semanal de ${year} frente a la media de ${years[0]} a ${years[years.length - 1]}`}>
    {weeks.filter(item => item.drop).map(item => <rect key={item.week} x={x(item.week) - 7} y={top} width={14} height={height - top - bottom} className="fill-error-bg" />)}
    {[0, 0.25, 0.5, 0.75, 1].map(value => <g key={value}>
      <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="stroke-border" />
      <text x={left - 6} y={y(value)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">{value * 100}%</text>
    </g>)}
    {MONTHS.map((month, index) => <text key={month} x={x(Math.round(index * 4.35) + 1)} y={height - 8} className="fill-muted-foreground text-[11px]">{month}</text>)}
    <path d={path('average')} fill="none" strokeWidth={2} strokeDasharray="5 4" className="stroke-muted-foreground opacity-70" />
    <path d={path('occupancy')} fill="none" strokeWidth={2} strokeLinejoin="round" className="stroke-primary" />
    {weeks.map(item => <rect key={item.week} x={x(item.week) - 8} y={top} width={16} height={height - top - bottom} fill="transparent">
      <title>{`Semana ${item.week} (${formatDate(item.start)}): ${year} ${percent(item.occupancy)} · media ${percent(item.average)}${item.drop ? ' · caída' : ''}`}</title>
    </rect>)}
  </svg>
}

export function BookingPace({ refreshVersion }: { refreshVersion: number }) {
  const [threshold, setThreshold] = useState('0.15')
  const [group, setGroup] = useState(ALL)
  const [view, setView] = useState<PaceView | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setError('')
    api<PaceView>(`/api/tariff-lab/booking-pace?threshold=${threshold}`)
      .then(result => { if (!cancelled) setView(result) })
      .catch(err => { if (!cancelled) setError((err as Error).message) })
    return () => { cancelled = true }
  }, [threshold, refreshVersion])

  const data = view?.groups[group] || view?.groups[ALL]
  const strategyGroups = Object.keys(view?.groups || {}).filter(name => name !== ALL)
  const drops = (data?.weeks || []).filter(item => item.drop).sort((a, b) => (b.drop || 0) - (a.drop || 0))
  const booked = data?.pace.reduce((sum, item) => sum + item.booked, 0) || 0
  const bookedBefore = data?.pace.reduce((sum, item) => sum + item.booked_previous, 0) || 0
  const behind = (data?.pace || []).filter(item => item.status === 'behind')
  const span = view ? `${view.comparison_years[0]}–${view.comparison_years[view.comparison_years.length - 1]}` : ''

  return <Card className="grid gap-5 p-4 sm:p-6">
    <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">COMPARATIVA</p>
      <h2 className="text-xl font-semibold">Ritmo de reservas y ofertas</h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Detecta las fechas en las que se reserva menos que en años anteriores y dónde una oferta o una bajada de precio puntual podría recuperar ocupación, sin tocar la tarifa SA del resto del año. Solo informa: no cambia tarifas ni publica nada en Avantio.</p>
    </div>
    {error && <Alert variant="destructive" role="alert">No se pudo calcular la comparativa: {error}</Alert>}
    {!view && !error && <p className="text-sm text-muted-foreground" role="status">Calculando la comparativa…</p>}
    {view && data && <>
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1.5 text-sm font-medium">Grupo SA<Select value={group} onChange={event => setGroup(event.target.value)}>{Object.keys(view.groups).map(name => <option key={name} value={name}>{name === ALL ? 'Todos los grupos' : name}</option>)}</Select></label>
        <label className="grid gap-1.5 text-sm font-medium">Caída mínima para marcar una semana<Select value={threshold} onChange={event => setThreshold(event.target.value)}>{['0.10', '0.15', '0.20', '0.25'].map(value => <option key={value} value={value}>{Math.round(Number(value) * 100)} puntos</option>)}</Select></label>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <div className="grid gap-0.5 rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Noches {view.next_year} ya reservadas</small><strong className="text-2xl tabular-nums">{booked.toLocaleString('es-ES')}</strong><span className={`text-xs ${bookedBefore && booked < bookedBefore ? 'text-error-ink' : 'text-muted-foreground'}`}>{bookedBefore ? `${percent(booked / bookedBefore - 1, true)} frente a ${view.year} en la misma fecha` : 'Sin referencia del año anterior'}</span></div>
        <div className="grid gap-0.5 rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Meses de {view.next_year} muy por detrás</small><strong className="text-2xl tabular-nums">{behind.length}</strong><span className="text-xs text-muted-foreground">{behind.length ? behind.map(item => MONTHS[item.month - 1]).join(', ') : 'Ninguno'}</span></div>
        <div className="grid gap-0.5 rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Semanas de {view.year} con caída</small><strong className="text-2xl tabular-nums">{drops.length}</strong><span className="text-xs text-muted-foreground">Más de {Math.round(view.threshold * 100)} puntos bajo la media {span}</span></div>
        {group !== ALL && <div className="grid gap-0.5 rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Crecimiento anual del precio</small><strong className="text-2xl tabular-nums">{percent(data.growth_4y, false, 1)}</strong><span className="text-xs text-muted-foreground">Precio real {view.year - 4}–{view.year}, compuesto</span></div>}
      </div>

      <div className="grid gap-2">
        <h3 className="text-base font-semibold">Ritmo de reservas {view.next_year} frente a {view.year}</h3>
        <p className="text-xs leading-5 text-muted-foreground">Noches de {view.next_year} reservadas hoy frente a las de {view.year} que estaban reservadas en la misma fecha del año pasado. Las cancelaciones posteriores no se ven en el corte del año pasado, así que la caída real puede ser algo mayor.</p>
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-primary" aria-hidden="true" />{view.next_year} reservado a {formatDate(view.as_of)}</span><span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-muted-foreground opacity-50" aria-hidden="true" />{view.year} a la misma fecha del año anterior</span></div>
        <PaceChart months={data.pace} year={view.year} nextYear={view.next_year} asOf={view.as_of} />
        <div className="max-w-full overflow-auto rounded-lg border border-border"><Table>
          <TableHeader><TableRow><TableHead>Mes</TableHead><TableHead className="text-right">{view.next_year} hoy</TableHead><TableHead className="text-right">{view.year} misma fecha</TableHead><TableHead className="text-right">Variación</TableHead><TableHead className="text-right">{view.year} final</TableHead><TableHead>Estado</TableHead><TableHead>Sugerencia</TableHead></TableRow></TableHeader>
          <TableBody>{data.pace.map(item => <TableRow key={item.month}>
            <TableCell className="whitespace-nowrap">{MONTHS[item.month - 1]} {view.next_year}</TableCell>
            <TableCell className="text-right tabular-nums">{item.booked}</TableCell>
            <TableCell className="text-right tabular-nums">{item.booked_previous}</TableCell>
            <TableCell className={`text-right tabular-nums ${item.change != null && item.change < 0 ? 'text-error-ink' : ''}`}>{percent(item.change, true)}</TableCell>
            <TableCell className="text-right tabular-nums">{item.final_previous}</TableCell>
            <TableCell><Badge className={statusLabels[item.status][1]}>{statusLabels[item.status][0]}</Badge></TableCell>
            <TableCell className="min-w-56 text-sm">{item.status === 'behind' ? 'Oferta de reserva anticipada (−5 a −10 %)' : item.status === 'slightly_behind' ? 'Vigilar y revisar en unas semanas' : '—'}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>
      </div>

      <div className="grid gap-2">
        <h3 className="text-base font-semibold">Semanas de {view.year} con menos ocupación</h3>
        <p className="text-xs leading-5 text-muted-foreground">Ocupación semanal frente a la media {span}: noches vendidas sobre noches disponibles, sin contar los bloqueos de propietario. Las semanas marcadas son candidatas a una oferta en las mismas fechas de los próximos años.</p>
        <div className="flex flex-wrap gap-4 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-primary" aria-hidden="true" />{view.year}</span><span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-muted-foreground opacity-70" aria-hidden="true" />Media {span}</span><span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-error-bg" aria-hidden="true" />Semana con caída</span></div>
        <WeeksChart weeks={data.weeks} year={view.year} years={view.comparison_years} />
        <div className="max-h-[28rem] max-w-full overflow-auto rounded-lg border border-border"><Table>
          <TableHeader><TableRow><TableHead>Semana</TableHead><TableHead>Fechas {view.year}</TableHead><TableHead className="text-right">Ocupación {view.year}</TableHead><TableHead className="text-right">Media {span}</TableHead><TableHead className="text-right">Diferencia</TableHead>{group !== ALL && <><TableHead className="text-right">Precio real {view.year}</TableHead><TableHead className="text-right">SA {view.next_year} → con oferta</TableHead>{view.projection && <TableHead className="text-right">Propuesta {view.projection.target_year}</TableHead>}</>}<TableHead>Oferta sugerida</TableHead></TableRow></TableHeader>
          <TableBody>{drops.map(item => <TableRow key={item.week}>
            <TableCell className="tabular-nums">{item.week}</TableCell>
            <TableCell className="whitespace-nowrap tabular-nums">{formatDate(item.start)} – {formatDate(item.end)}</TableCell>
            <TableCell className="text-right tabular-nums">{percent(item.occupancy)}</TableCell>
            <TableCell className="text-right tabular-nums">{percent(item.average)}</TableCell>
            <TableCell className="whitespace-nowrap text-right tabular-nums text-error-ink">{points(item.drop || 0)}</TableCell>
            {group !== ALL && <>
              <TableCell className="text-right tabular-nums">{price(item.real_price)}</TableCell>
              <TableCell className="whitespace-nowrap text-right tabular-nums">{item.sa_next ? <>{price(item.sa_next)} → <strong>{price(item.offer?.rate)}</strong></> : '—'}</TableCell>
              {view.projection && <TableCell className="text-right tabular-nums">{price(item.projected)}</TableCell>}
            </>}
            <TableCell className="min-w-56 text-sm">{item.offer?.label}</TableCell>
          </TableRow>)}
            {!drops.length && <TableRow><TableCell colSpan={9} className="py-6 text-center text-sm text-muted-foreground">No hay semanas con una caída de más de {Math.round(view.threshold * 100)} puntos.</TableCell></TableRow>}
          </TableBody>
        </Table></div>
        <p className="text-xs leading-5 text-muted-foreground">{group === ALL ? 'Elige un grupo SA para ver su precio real, la tarifa SA con la oferta aplicada y la propuesta de la última proyección. ' : ''}{data.units < 3 ? 'Este grupo tiene pocos alojamientos: su ocupación es poco representativa, tómala solo como indicio. ' : ''}Oferta de partida según la caída: {offerRanges(view)}. Es una propuesta para revisar; no se aplica sola.</p>
      </div>

      <div className="grid gap-2">
        <h3 className="text-base font-semibold">Cómo se han fijado los precios</h3>
        <p className="text-xs leading-5 text-muted-foreground">Precio real: mediana de lo cobrado por noche en las mismas fechas cada año (con IVA, estancias de menos de 28 noches). Variación anual: mediana de las temporadas. SA {view.next_year} / real {view.year}: cuánto se fijó la tarifa por encima de lo cobrado en las mismas fechas.</p>
        <div className="max-w-full overflow-auto rounded-lg border border-border"><Table>
          <TableHeader><TableRow><TableHead>Grupo SA</TableHead><TableHead className="text-right">Aloj.</TableHead><TableHead className="text-right">Crecimiento anual {view.year - 4}–{view.year}</TableHead>{[view.year - 2, view.year - 1, view.year].map(year => <TableHead key={year} className="text-right">{year}</TableHead>)}<TableHead className="text-right">SA {view.next_year} / real {view.year}</TableHead><TableHead className="text-right">SA {view.next_year} media</TableHead>{view.projection && <><TableHead className="text-right">Propuesta {view.projection.target_year} media</TableHead><TableHead className="text-right">Variación</TableHead></>}</TableRow></TableHeader>
          <TableBody>{strategyGroups.map(name => {
            const item = view.groups[name]
            const projectedChange = item.projected_average && item.sa_next_average ? Number(item.projected_average) / Number(item.sa_next_average) - 1 : null
            return <TableRow key={name} className={name === group ? 'bg-accent' : ''}>
              <TableCell className="min-w-48">{name}</TableCell>
              <TableCell className="text-right tabular-nums">{item.units}</TableCell>
              <TableCell className="text-right tabular-nums">{percent(item.growth_4y, true, 1)}</TableCell>
              {[view.year - 2, view.year - 1, view.year].map(year => <TableCell key={year} className="text-right tabular-nums">{percent(item.yearly_change?.[String(year)], true, 1)}</TableCell>)}
              <TableCell className="text-right tabular-nums">{item.sa_next_vs_real ? `×${item.sa_next_vs_real.toFixed(2).replace('.', ',')}` : '—'}</TableCell>
              <TableCell className="text-right tabular-nums">{price(item.sa_next_average)}</TableCell>
              {view.projection && <><TableCell className="text-right tabular-nums">{price(item.projected_average)}</TableCell><TableCell className="text-right tabular-nums">{percent(projectedChange, true, 1)}</TableCell></>}
            </TableRow>
          })}</TableBody>
        </Table></div>
        {view.projection && <p className="text-xs text-muted-foreground">Propuesta: última proyección con año base {view.next_year} («{view.projection.name}»), con los cambios manuales que tenga.</p>}
      </div>
    </>}
  </Card>
}
