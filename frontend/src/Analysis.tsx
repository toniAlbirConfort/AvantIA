import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Info, TrendingDown, TrendingUp } from 'lucide-react'
import { api } from './api'
import type { AnalysisInsight, AnalysisMonth, AnalysisSegmentRow, AnalysisYear, ReservationAnalysis } from './types'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Select } from './components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

type Ready = Extract<ReservationAnalysis, { has_data: true }>
type SegmentKey = keyof Ready['segments']

const MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
const SEGMENTS: { id: SegmentKey; label: string }[] = [
  { id: 'group', label: 'Edificio y capacidad' },
  { id: 'channel', label: 'Canal de venta' },
  { id: 'stay', label: 'Duración de la estancia' },
  { id: 'guests', label: 'Tipo de huésped' },
  { id: 'lead', label: 'Antelación de la reserva' },
]

const integer = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('es-ES', { maximumFractionDigits: 1 })
const euros = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })
const eurosCents = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', minimumFractionDigits: 2, maximumFractionDigits: 2 })

const pct = (value: number | null | undefined) => value == null ? '—' : `${decimal.format(value * 100)} %`
const money = (value: number | null | undefined) => value == null ? '—' : euros.format(value)
const num = (value: number | null | undefined) => value == null ? '—' : integer.format(value)
const signed = (value: number | null | undefined, suffix = ' %') => value == null ? '—' : `${value > 0 ? '+' : ''}${decimal.format(value)}${suffix}`
const change = (current: number | null, previous: number | null) => current == null || !previous ? null : (current - previous) / previous * 100

function displayDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function windowLabel(end: string) {
  const [month, day] = end.split('-').map(Number)
  const last = new Date(2001, month - 1, day - 1)
  return `1 ene al ${last.getDate()} ${MONTHS[last.getMonth()].toLowerCase()}`
}

function Delta({ value, suffix = ' %', positiveIsGood = true }: { value: number | null; suffix?: string; positiveIsGood?: boolean }) {
  if (value == null) return <span className="text-xs text-muted-foreground">sin comparación</span>
  const good = value === 0 ? null : (value > 0) === positiveIsGood
  const Icon = value >= 0 ? TrendingUp : TrendingDown
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${good == null ? 'bg-muted text-muted-foreground' : good ? 'bg-success-bg text-success-ink' : 'bg-error-bg text-error-ink'}`}>
    <Icon size={13} aria-hidden="true" />{signed(value, suffix)}
  </span>
}

function Kpis({ current, previous }: { current: AnalysisYear; previous?: AnalysisYear }) {
  const items = [
    { label: 'Ocupación', value: pct(current.occupancy), delta: previous && current.occupancy != null && previous.occupancy != null ? (current.occupancy - previous.occupancy) * 100 : null, suffix: ' pts' },
    { label: 'Precio medio por noche', value: money(current.adr), delta: change(current.adr, previous?.adr ?? null) },
    { label: 'Ingreso por alojamiento y día', value: current.revpar == null ? '—' : eurosCents.format(current.revpar), delta: change(current.revpar, previous?.revpar ?? null) },
    { label: 'Ingreso del periodo', value: money(current.revenue), delta: change(current.revenue, previous?.revenue ?? null) },
    { label: 'Noches vendidas', value: num(current.nights), delta: change(current.nights, previous?.nights ?? null) },
  ]
  return <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
    {items.map(item => <Card key={item.label} className="p-4 sm:p-5">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{item.label}</p>
      <strong className="my-2 block text-3xl font-semibold tabular-nums">{item.value}</strong>
      <div className="flex flex-wrap items-center gap-2"><Delta value={item.delta} suffix={item.suffix} />{previous && <span className="text-xs text-muted-foreground">vs {previous.year}</span>}</div>
    </Card>)}
  </div>
}

const insightStyle: Record<AnalysisInsight['severity'], { icon: typeof Info; label: string; className: string }> = {
  warning: { icon: AlertTriangle, label: 'Atención', className: 'border-warning-ink/25 bg-warning-bg text-warning-ink' },
  positive: { icon: TrendingUp, label: 'Mejora', className: 'border-success-ink/25 bg-success-bg text-success-ink' },
  info: { icon: Info, label: 'Oportunidad', className: 'border-info-ink/25 bg-info-bg text-info-ink' },
}

function Insights({ insights }: { insights: AnalysisInsight[] }) {
  return <Card className="p-4 sm:p-6">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">DIFERENCIAS ENTRE AÑOS</p>
    <h2 className="mb-4 text-xl font-semibold">Necesidades detectadas</h2>
    {insights.length ? <ul className="grid gap-3 lg:grid-cols-2">
      {insights.map(item => {
        const style = insightStyle[item.severity]
        return <li key={item.title} className={`flex gap-3 rounded-lg border p-3 sm:p-4 ${style.className}`}>
          <style.icon size={18} className="mt-0.5 shrink-0" aria-hidden="true" />
          <div className="min-w-0"><p className="text-sm font-semibold"><span className="sr-only">{style.label}: </span>{item.title}</p><p className="mt-1 text-sm leading-6">{item.detail}</p></div>
        </li>
      })}
    </ul> : <p className="text-sm text-muted-foreground">No se aprecian diferencias relevantes entre los dos últimos años.</p>}
  </Card>
}

function YearTable({ data }: { data: Ready }) {
  const rows: { label: string; render: (year: AnalysisYear) => string }[] = [
    { label: 'Noches vendidas', render: year => num(year.nights) },
    { label: 'Ocupación', render: year => pct(year.occupancy) },
    { label: 'Precio medio por noche', render: year => money(year.adr) },
    { label: 'Ingreso por alojamiento y día', render: year => year.revpar == null ? '—' : eurosCents.format(year.revpar) },
    { label: 'Ingreso', render: year => money(year.revenue) },
    { label: 'Llegadas', render: year => num(year.arrivals) },
    { label: 'Estancia mediana (noches)', render: year => year.median_stay == null ? '—' : decimal.format(year.median_stay) },
    { label: 'Antelación mediana (días)', render: year => num(year.median_lead_days) },
    { label: 'Tasa de cancelación', render: year => pct(year.cancellation_rate) },
    { label: 'Noches de estancias de 28+', render: year => pct(year.long_stay_share) },
    { label: 'Reservas creadas', render: year => num(year.bookings_created) },
    { label: 'Peticiones de información', render: year => num(year.inquiries) },
  ]
  return <Card className="overflow-hidden p-4 sm:p-6">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">MISMA VENTANA · DEL {windowLabel(data.window.end).toUpperCase()}</p>
    <h2 className="mb-4 text-xl font-semibold">Comparativa interanual</h2>
    <div className="max-w-full overflow-auto rounded-lg border border-border">
      <Table>
        <TableHeader><TableRow><TableHead className="min-w-52">Indicador</TableHead>{data.yearly.map(year => <TableHead key={year.year} className="text-right">{year.year}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{rows.map(row => <TableRow key={row.label}>
          <TableCell className="font-medium">{row.label}</TableCell>
          {data.yearly.map(year => <TableCell key={year.year} className="whitespace-nowrap text-right tabular-nums">{row.render(year)}</TableCell>)}
        </TableRow>)}</TableBody>
      </Table>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">Reservas pagadas y confirmadas. El ingreso es el total sin tasas de cada estancia repartido por noche; incluye la comisión de los portales.</p>
  </Card>
}

function MonthCell({ month, metric, maxAdr }: { month: AnalysisMonth; metric: 'occupancy' | 'adr'; maxAdr: number }) {
  const value = metric === 'occupancy' ? month.occupancy : month.adr
  const intensity = value == null ? 0 : Math.round(Math.min(metric === 'occupancy' ? value : value / (maxAdr || 1), 1) * 80)
  const forward = month.state === 'forward'
  const text = metric === 'occupancy' ? (value == null ? '—' : `${integer.format(value * 100)}`) : (value == null ? '—' : integer.format(value))
  return <TableCell
    className={`whitespace-nowrap px-2 text-center text-sm tabular-nums ${forward ? 'italic text-muted-foreground' : 'font-medium'}`}
    style={{ backgroundColor: `color-mix(in oklab, var(--brand-turquoise) ${forward ? Math.round(intensity / 2) : intensity}%, transparent)` }}
    title={`${MONTHS[month.month - 1]} · ${month.nights} noches${forward ? ' ya reservadas' : month.state === 'partial' ? ' (mes en curso)' : ''}`}
  >{text}{month.state !== 'closed' && <span className="text-[0.65rem]" aria-hidden="true">*</span>}</TableCell>
}

function MonthlyTable({ data }: { data: Ready }) {
  const [metric, setMetric] = useState<'occupancy' | 'adr'>('occupancy')
  const maxAdr = Math.max(0, ...data.monthly.flatMap(year => year.months.map(month => month.adr || 0)))
  return <Card className="overflow-hidden p-4 sm:p-6">
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">ESTACIONALIDAD</p><h2 className="text-xl font-semibold">{metric === 'occupancy' ? 'Ocupación mensual (%)' : 'Precio medio mensual (€)'}</h2></div>
      <div className="flex rounded-md border border-border bg-background p-1" role="group" aria-label="Indicador mensual">
        <Button variant={metric === 'occupancy' ? 'secondary' : 'ghost'} size="sm" aria-pressed={metric === 'occupancy'} onClick={() => setMetric('occupancy')}>Ocupación</Button>
        <Button variant={metric === 'adr' ? 'secondary' : 'ghost'} size="sm" aria-pressed={metric === 'adr'} onClick={() => setMetric('adr')}>Precio medio</Button>
      </div>
    </div>
    <div className="max-w-full overflow-auto rounded-lg border border-border">
      <Table>
        <TableHeader><TableRow><TableHead>Año</TableHead>{MONTHS.map(month => <TableHead key={month} className="px-2 text-center">{month}</TableHead>)}</TableRow></TableHeader>
        <TableBody>{data.monthly.map(year => <TableRow key={year.year}>
          <TableCell className="font-semibold tabular-nums">{year.year}</TableCell>
          {year.months.map(month => <MonthCell key={month.month} month={month} metric={metric} maxAdr={maxAdr} />)}
        </TableRow>)}</TableBody>
      </Table>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">Meses completos. * Mes en curso o futuro: solo cuenta lo ya reservado a la fecha de los datos, en cursiva.</p>
  </Card>
}

function PaceTable({ data }: { data: Ready }) {
  const rows = data.pace
  const reference = (key: 'rest_of_year' | 'next_year') => {
    const values = rows.slice(0, -1).map(row => row[key].on_books).filter(Boolean)
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  }
  const last = rows[rows.length - 1]
  return <Card className="overflow-hidden p-4 sm:p-6">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">RITMO DE RESERVAS</p>
    <h2 className="mb-1 text-xl font-semibold">Noches reservadas a la misma fecha</h2>
    <p className="mb-4 text-sm leading-6 text-muted-foreground">Compara lo que ya estaba vendido en cada año en la misma fecha de corte con lo que se terminó vendiendo.</p>
    {last && <div className="mb-4 grid gap-3 sm:grid-cols-2">
      {(['rest_of_year', 'next_year'] as const).map(key => <div key={key} className="rounded-lg border border-border p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{key === 'rest_of_year' ? `Resto de ${last.year}` : `Año ${last.year + 1}`}</p>
        <strong className="my-1 block text-2xl font-semibold tabular-nums">{num(last[key].on_books)} noches</strong>
        <div className="flex flex-wrap items-center gap-2"><Delta value={change(last[key].on_books, reference(key))} /><span className="text-xs text-muted-foreground">vs media de años anteriores ({num(reference(key))})</span></div>
      </div>)}
    </div>}
    <div className="max-w-full overflow-auto rounded-lg border border-border">
      <Table>
        <TableHeader><TableRow><TableHead>Fecha de corte</TableHead><TableHead className="text-right">Resto del año · reservado</TableHead><TableHead className="text-right">Resto del año · final</TableHead><TableHead className="text-right">Año siguiente · reservado</TableHead><TableHead className="text-right">Año siguiente · final</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(row => <TableRow key={row.year}>
          <TableCell className="whitespace-nowrap tabular-nums">{displayDate(row.as_of)}</TableCell>
          <TableCell className="text-right tabular-nums">{num(row.rest_of_year.on_books)} <span className="text-xs text-muted-foreground">({pct(row.rest_of_year.occupancy)})</span></TableCell>
          <TableCell className="text-right tabular-nums">{num(row.rest_of_year.final)}</TableCell>
          <TableCell className="text-right tabular-nums">{num(row.next_year.on_books)} <span className="text-xs text-muted-foreground">({pct(row.next_year.occupancy)})</span></TableCell>
          <TableCell className="text-right tabular-nums">{num(row.next_year.final)}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">Solo cuenta reservas que siguen vigentes hoy: las que se cancelaron después no figuran, así que los años anteriores pueden estar algo infravalorados.</p>
  </Card>
}

function SegmentTable({ data }: { data: Ready }) {
  const [segment, setSegment] = useState<SegmentKey>('stay')
  const rows: AnalysisSegmentRow[] = data.segments[segment]
  const lastYear = String(data.years[data.years.length - 1])
  const withUnits = segment === 'group'
  return <Card className="overflow-hidden p-4 sm:p-6">
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">DÓNDE ESTÁ LA DIFERENCIA</p><h2 className="text-xl font-semibold">Noches y precio medio por segmento</h2></div>
      <label className="grid w-full gap-1.5 text-sm font-medium sm:w-72">Segmento<Select value={segment} onChange={event => setSegment(event.target.value as SegmentKey)}>{SEGMENTS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</Select></label>
    </div>
    <div className="max-w-full overflow-auto rounded-lg border border-border">
      <Table>
        <TableHeader><TableRow>
          <TableHead className="min-w-48">Segmento</TableHead>
          {data.years.map(year => <TableHead key={year} className="text-right">{year}<span className="block text-[0.7rem] font-normal">noches · €/noche{withUnits ? ' · ocup.' : ''}</span></TableHead>)}
          <TableHead className="min-w-40">Peso {lastYear}</TableHead>
          <TableHead className="text-right">Noches vs año anterior</TableHead>
        </TableRow></TableHeader>
        <TableBody>{rows.map(row => {
          const share = row.values[lastYear]?.share ?? 0
          return <TableRow key={row.key}>
            <TableCell className="font-medium">{row.key}{withUnits && <span className="block text-xs text-muted-foreground">{row.units} alojamientos</span>}</TableCell>
            {data.years.map(year => {
              const value = row.values[String(year)]
              return <TableCell key={year} className="whitespace-nowrap text-right tabular-nums">{num(value?.nights)} <span className="text-xs text-muted-foreground">· {money(value?.adr)}{withUnits ? ` · ${pct(value?.occupancy)}` : ''}</span></TableCell>
            })}
            <TableCell><div className="flex items-center gap-2"><div className="h-2 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true"><div className="h-full rounded-full bg-brand-turquoise" style={{ width: `${Math.round(share * 100)}%` }} /></div><span className="w-12 text-right text-xs tabular-nums">{pct(share)}</span></div></TableCell>
            <TableCell className="text-right"><Delta value={row.nights_change == null ? null : row.nights_change * 100} /></TableCell>
          </TableRow>
        })}</TableBody>
      </Table>
    </div>
  </Card>
}

function DataQuality({ data }: { data: Ready }) {
  const quality = data.data_quality
  const items = [
    { label: 'Peticiones de información con fechas pasadas sin cerrar', value: num(quality.stale_inquiries) },
    { label: 'Estancias terminadas con importe pendiente', value: `${num(quality.unpaid_past_stays)} · ${money(quality.unpaid_past_amount)}` },
    { label: 'Estancias terminadas que siguen como «Confirmada»', value: num(quality.confirmed_past_stays) },
    { label: 'Reservas con más huéspedes que la capacidad', value: num(quality.over_capacity) },
    { label: 'Reservas vendidas con importe 0 €', value: num(quality.zero_value_sold) },
    { label: 'Huecos de 1 a 3 noches en los últimos 12 meses', value: `${num(data.orphan_gaps.gaps)} · ${num(data.orphan_gaps.nights)} noches` },
  ]
  return <Card className="p-4 sm:p-6">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">REVISAR EN AVANTIO</p>
    <h2 className="mb-4 text-xl font-semibold">Calidad de los datos</h2>
    <dl className="grid gap-x-6 sm:grid-cols-2 xl:grid-cols-3">{items.map(item => <div key={item.label} className="flex items-baseline justify-between gap-3 border-t border-border py-3">
      <dt className="text-sm text-muted-foreground">{item.label}</dt><dd className="shrink-0 text-sm font-semibold tabular-nums">{item.value}</dd>
    </div>)}</dl>
  </Card>
}

export function Analysis({ refreshVersion }: { refreshVersion: number }) {
  const [years, setYears] = useState(4)
  const [data, setData] = useState<ReservationAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api<ReservationAnalysis>(`/api/analysis?years=${years}`))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [years])

  useEffect(() => { void load() }, [load, refreshVersion])

  if (loading && !data) return <Card className="p-5 text-sm text-muted-foreground" role="status">Calculando el análisis de reservas…</Card>
  if (error) return <Card className="grid gap-4 p-5 sm:p-6">
    <Alert variant="destructive">No se pudo calcular el análisis: {error}</Alert>
    <Button type="button" variant="secondary" className="w-fit" onClick={() => void load()}>Reintentar</Button>
  </Card>
  if (!data) return null
  if (!data.has_data) return <Card className="p-5 text-sm leading-6 text-muted-foreground">
    Para analizar las reservas hace falta importar {data.missing.map(item => item === 'reservations' ? 'el listado de reservas' : 'el listado de propiedades').join(' y ')} desde Importaciones.
  </Card>

  const current = data.yearly[data.yearly.length - 1]
  const previous = data.yearly[data.yearly.length - 2]
  return <div className="grid gap-4" aria-busy={loading}>
    <Card className="flex flex-wrap items-end justify-between gap-4 p-4 sm:p-5">
      <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
        Listado de reservas importado el <strong className="text-foreground">{displayDate(data.as_of)}</strong> · {data.units} alojamientos activos · cada año se compara del {windowLabel(data.window.end)} para que el año en curso no se mida contra años completos.
      </p>
      <label className="grid w-full gap-1.5 text-sm font-medium sm:w-48">Años comparados<Select value={years} onChange={event => setYears(Number(event.target.value))} disabled={loading}>{[2, 3, 4, 5, 6, 7, 8].map(value => <option key={value} value={value}>{value} años</option>)}</Select></label>
    </Card>
    <Kpis current={current} previous={previous} />
    <Insights insights={data.insights} />
    <YearTable data={data} />
    <MonthlyTable data={data} />
    <div className="grid items-start gap-4 2xl:grid-cols-2"><PaceTable data={data} /><DataQuality data={data} /></div>
    <SegmentTable data={data} />
  </div>
}
