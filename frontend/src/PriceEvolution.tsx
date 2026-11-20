import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

type RealPrice = { rate: string; nights: number; partial: boolean; few_nights: boolean }
type SaPrice = { rate: string; complete: boolean }
type EvolutionSeason = {
  key: string; tariff_name: string; start: string; end: string; note: string | null; rate: string
  years: { year: number; real: RealPrice | null; sa: SaPrice | null }[]
  change_vs_base_sa: number | null; change_vs_last_real: number | null
}
type AnnualPoint = { year: number; kind: 'real' | 'sa' | 'projection'; rate: string; partial?: boolean; few_nights?: boolean; complete?: boolean }
export type Evolution = {
  base_year: number; target_year: number; season_years: number[]; min_nights: number; as_of: string
  seasons: EvolutionSeason[]; annual: Record<string, AnnualPoint[]>
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function price(value: string | null | undefined, decimals = 2) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Number(value))
}

function change(value: number | null) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 1, signDisplay: 'exceptZero' }).format(value)
}

const kindLabels = { real: 'Precio real (mediana por noche)', sa: 'Tarifa SA media', projection: 'Propuesta media' }

function niceTicks(min: number, max: number) {
  const span = Math.max(max - min, 1)
  const step = [10, 20, 25, 50, 100].find(item => span / item <= 4) ?? 200
  const first = Math.floor(min / step) * step
  const ticks = []
  for (let value = first; value <= max + step; value += step) ticks.push(value)
  return ticks.filter(value => value >= first && value <= Math.ceil(max / step) * step)
}

function EvolutionChart({ group, points }: { group: string; points: AnnualPoint[] }) {
  if (points.length < 2) return <p className="text-xs text-muted-foreground">No hay años suficientes para dibujar la evolución.</p>
  const width = 640, height = 200, left = 44, right = 76, top = 16, bottom = 28
  const values = points.map(item => Number(item.rate))
  const ticks = niceTicks(Math.min(...values), Math.max(...values))
  const low = ticks[0], high = ticks[ticks.length - 1]
  const years = points.map(item => item.year)
  const x = (year: number) => left + (years.length === 1 ? 0 : ((year - years[0]) / (years[years.length - 1] - years[0])) * (width - left - right))
  const y = (value: number) => top + (1 - (value - low) / (high - low || 1)) * (height - top - bottom)
  const real = points.filter(item => item.kind === 'real')
  const lastReal = real[real.length - 1]
  const tail = [lastReal, ...points.filter(item => item.kind !== 'real')].filter(Boolean)
  const path = (items: AnnualPoint[]) => items.map((item, index) => `${index ? 'L' : 'M'}${x(item.year).toFixed(1)},${y(Number(item.rate)).toFixed(1)}`).join(' ')
  const labelled = new Set([lastReal?.year, ...points.filter(item => item.kind !== 'real').map(item => item.year)])
  return <figure className="grid gap-1.5">
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full max-w-3xl text-primary" role="img" aria-label={`Evolución del precio por noche de ${group}: ${points.map(item => `${item.year} ${price(item.rate, 0)}`).join(', ')}`}>
      {ticks.map(value => <g key={value}>
        <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="stroke-border" strokeWidth={1} />
        <text x={left - 6} y={y(value)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[11px] tabular-nums">{value} €</text>
      </g>)}
      {years.map(year => <text key={year} x={x(year)} y={height - 8} textAnchor="middle" className="fill-muted-foreground text-[11px] tabular-nums">{year}</text>)}
      {real.length > 1 && <path d={path(real)} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
      {tail.length > 1 && <path d={path(tail)} fill="none" stroke="currentColor" strokeWidth={2} strokeDasharray="5 4" strokeLinecap="round" />}
      {points.map(item => <g key={item.year}>
        <title>{`${item.year} · ${kindLabels[item.kind]}: ${price(item.rate)}${item.partial ? ' (año en curso)' : ''}${item.few_nights ? ' (pocas noches vendidas)' : ''}`}</title>
        <circle cx={x(item.year)} cy={y(Number(item.rate))} r={12} fill="transparent" />
        {item.kind === 'real'
          ? <circle cx={x(item.year)} cy={y(Number(item.rate))} r={4.5} className="fill-current stroke-card" strokeWidth={2} />
          : <circle cx={x(item.year)} cy={y(Number(item.rate))} r={4.5} className="fill-card stroke-current" strokeWidth={2} />}
        {labelled.has(item.year) && <text x={x(item.year) + 8} y={y(Number(item.rate)) - 8} className="fill-foreground text-[11px] font-semibold tabular-nums">{price(item.rate, 0)}</text>}
      </g>)}
    </svg>
    <figcaption className="text-xs text-muted-foreground">Línea continua: precio real cobrado (mediana por noche). Discontinua: tarifa SA media y propuesta media, ponderadas por días.</figcaption>
  </figure>
}

export function PriceEvolution({ evolution }: { evolution: Evolution }) {
  const groups = Object.keys(evolution.annual)
  const pastYears = evolution.season_years.filter(year => year < evolution.base_year)
  return <div className="grid gap-4">
    <div className="grid gap-1">
      <h3 className="text-base font-semibold">Evolución de precios</h3>
      <p className="text-xs leading-5 text-muted-foreground">Mismas fechas en cada año. Antes de {evolution.base_year} solo hay precio real: la mediana de lo cobrado por noche (con IVA, estancias de menos de 28 noches), porque Tarifas no guarda tarifas SA anteriores; si existen, aparecen debajo. «En curso»: la temporada aún no ha terminado. «Pocas noches»: menos de {evolution.min_nights} noches vendidas, dato poco fiable.</p>
    </div>
    {groups.map(group => {
      const rows = evolution.seasons.filter(item => item.tariff_name === group)
      return <div key={group} className="grid gap-2">
        <h4 className="text-sm font-semibold">{group}</h4>
        <EvolutionChart group={group} points={evolution.annual[group]} />
        <div className="max-w-full overflow-auto rounded-lg border border-border"><Table>
          <TableHeader><TableRow>
            <TableHead>Fecha inicio</TableHead><TableHead>Última noche</TableHead>
            {pastYears.map(year => <TableHead key={year} className="text-right">Real {year}</TableHead>)}
            <TableHead className="text-right">SA {evolution.base_year}</TableHead>
            <TableHead className="text-right">Propuesta {evolution.target_year}</TableHead>
            <TableHead className="text-right">vs SA {evolution.base_year}</TableHead>
            <TableHead className="text-right">vs último real</TableHead>
          </TableRow></TableHeader>
          <TableBody>{rows.map(row => <TableRow key={row.key}>
            <TableCell className="whitespace-nowrap tabular-nums">{formatDate(row.start)}{row.note && <small className="block text-xs text-muted-foreground">{row.note === 'Semana Santa' ? 'Semana Santa' : 'Sin Semana Santa'}</small>}</TableCell>
            <TableCell className="whitespace-nowrap tabular-nums">{formatDate(row.end)}</TableCell>
            {row.years.filter(item => item.year < evolution.base_year).map(item => <TableCell key={item.year} className="whitespace-nowrap text-right tabular-nums">
              <span className={item.real?.few_nights ? 'text-muted-foreground' : ''}>{price(item.real?.rate)}</span>
              {(item.real?.partial || item.real?.few_nights) && <small className="block text-xs text-warning-ink">{[item.real.partial && 'En curso', item.real.few_nights && 'Pocas noches'].filter(Boolean).join(' · ')}</small>}
              {item.sa && <small className="block text-xs text-muted-foreground">SA {price(item.sa.rate)}{item.sa.complete ? '' : ' (parcial)'}</small>}
            </TableCell>)}
            <TableCell className="whitespace-nowrap text-right tabular-nums">{price(row.years[row.years.length - 1]?.sa?.rate)}</TableCell>
            <TableCell className="whitespace-nowrap text-right font-semibold tabular-nums">{price(row.rate)}</TableCell>
            <TableCell className="text-right tabular-nums">{change(row.change_vs_base_sa)}</TableCell>
            <TableCell className="text-right tabular-nums">{change(row.change_vs_last_real)}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>
      </div>
    })}
  </div>
}
