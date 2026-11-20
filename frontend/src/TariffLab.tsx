import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { api, apiDownload } from './api'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'
import { BookingPace } from './BookingPace'
import { TariffProjections } from './TariffProjections'

type ReadinessCheck = { key: string; ready: boolean; count?: number; name?: string | null; unmapped?: { id: string; name: string; city: string | null }[] }
type BusinessScope = { country: string; autonomous_community: string; province: string; municipality: string; locality: string; label: string }
type Readiness = {
  can_generate: boolean; groups: string[]; active_properties: number
  unmapped_properties: { id: string; name: string; city: string | null }[]
  uncertain_properties: { id: string; name: string; tariff_name: string }[]
  unmapped_groups: string[]; scope: BusinessScope
  reservation_coverage: { from: string; to: string } | null
  availability_coverage: { from: string; to: string } | null
  checks: ReadinessCheck[]; algorithm_version: string; price_change_limit_percent: number
}
type PropertyGroup = {
  property_id: string; name: string; building: string | null; capacity: number
  tariff_name: string | null; confidence: 'alta' | 'media' | 'baja' | null; method: string | null
  priced_stays: number; price_error: number | null; runner_up: string | null; reason: string
}
type Holiday = {
  id: number; date: string; name: string; scope: string; region: string | null; municipality: string | null
  source: string; source_reference: string | null; source_url: string | null
}
type HolidaySync = {
  last_run_at: string | null; last_success_at: string | null; error: string | null; warnings: string[]
  coverage: { year: number; national: number; regional: number; local: number }[]
}
type Study = {
  id: number; name: string; period_start: string; period_end: string; guests: number; nights: number
  tariff_names: string[]; parameters: Record<string, unknown>; status: string; error: string | null
  algorithm_version: string; created_at: string | null; result?: StudyResult | null
}
type DailyRate = {
  date: string; tariff_name: string; current_rate: string | null; historical_adr: string | null
  historical_occupancy: number | null; historical_revpar: string | null; lead_time_days: number | null
  length_of_stay: number | null; target_occupancy: number | null; market_median: string | null
  market_sample_count: number; holiday_names: string[]; recommended_rate: string | null
  confidence: string; reason: string | null; occupancy_reliable: boolean; history_sample_days: number
}
type ProposedSeason = { tariff_name: string; rate_plan: string; start_date: string; end_date: string; nightly_rate: string; day_count: number }
type StudyResult = {
  provider: string; market_sources: string[]; comparables: { name: string; url: string | null }[]
  captured_at: string; method: Record<string, string>; daily: DailyRate[]
  history_coverage: Record<string, { years: number[]; days_with_inventory: number; days_expected: number; sold_nights: number; uncovered_sold_nights: number; sellable_nights: number }>
  seasons: ProposedSeason[]; gaps: { date: string; tariff_name: string; reason: string }[]
  warnings: string[]; generated_days: number; missing_days: number; status: string
}

const checkLabels: Record<string, string> = {
  properties: 'Catálogo activo', group_mapping: 'Vinculación con grupos SA', reservations: 'Histórico de reservas',
  availability_history: 'Histórico de disponibilidad', market_provider: 'Proveedor de mercado autorizado',
  holiday_calendar: 'Calendario de festivos',
}
const scopeLabels: Record<string, string> = { national: 'Nacional', regional: 'Autonómico', municipal: 'Local' }
const sourceLabels: Record<string, string> = { boe: 'BOE', dogv: 'DOGV', ayuntamiento: 'Ayuntamiento' }
const confidenceStyles: Record<string, string> = {
  alta: 'bg-success-bg text-success-ink', media: 'bg-warning-bg text-warning-ink', baja: 'bg-warning-bg text-warning-ink',
}
const statusLabels: Record<string, string> = {
  pending: 'En cola', running: 'Calculando', completed: 'Completo', partial: 'Parcial', failed: 'Error',
}
const gapLabels: Record<string, string> = {
  no_property_mapping: 'Sin alojamientos asignados', no_current_sa_reference: 'Sin referencia SA',
  no_historical_adr: 'Falta histórico de ADR',
  occupancy_not_reliable: 'Ocupación no fiable: falta inventario completo; ajuste omitido',
  insufficient_comparable_market_rates: 'Faltan comparables de mercado',
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function money(value: string | null | undefined) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(Number(value))
}

const POLL_INTERVAL_MS = 12000
const SYNC_WAIT_LIMIT_MS = 15 * 60 * 1000

function percentage(value: number | null | undefined) {
  return value == null ? '—' : new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 0 }).format(value)
}

export function TariffLab({ role, refreshVersion }: { role: string; refreshVersion: number }) {
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [propertyGroups, setPropertyGroups] = useState<PropertyGroup[]>([])
  const [holidays, setHolidays] = useState<Holiday[]>([])
  const [holidaySync, setHolidaySync] = useState<HolidaySync | null>(null)
  const [holidayYear, setHolidayYear] = useState(() => String(new Date().getFullYear()))
  const [studies, setStudies] = useState<Study[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selectedIdRef = useRef<number | null>(null)
  selectedIdRef.current = selectedId
  const [selectedStudy, setSelectedStudy] = useState<Study | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const admin = role === 'admin'

  // Readiness and property groups are computed from the whole reservation history,
  // so they load on demand only: on opening the view, with the refresh buttons and
  // when a requested holiday sync finishes. Only pending work is polled.
  const [syncWait, setSyncWait] = useState<{ lastRunAt: string | null; since: number } | null>(null)

  const loadStatus = useCallback(async () => {
    const [nextReadiness, nextGroups, nextHolidays, nextSync] = await Promise.all([
      api<Readiness>('/api/tariff-lab/readiness'),
      api<PropertyGroup[]>('/api/tariff-lab/property-groups'),
      api<Holiday[]>('/api/tariff-lab/holidays'),
      api<HolidaySync>('/api/tariff-lab/holidays/sync'),
    ])
    setReadiness(nextReadiness)
    setPropertyGroups(nextGroups)
    setHolidays(nextHolidays)
    setHolidaySync(nextSync)
  }, [])

  const loadStudies = useCallback(async () => {
    const nextStudies = await api<Study[]>('/api/tariff-lab/studies')
    setStudies(nextStudies)
    const currentId = selectedIdRef.current ?? nextStudies[0]?.id ?? null
    if (currentId == null) return
    if (selectedIdRef.current == null) setSelectedId(currentId)
    setSelectedStudy(await api<Study>(`/api/tariff-lab/studies/${currentId}`))
  }, [])

  const load = useCallback(async () => {
    try {
      await Promise.all([loadStatus(), loadStudies()])
      setError('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [loadStatus, loadStudies])

  useEffect(() => { void load() }, [load, refreshVersion])

  const studyInProgress = studies.some(item => item.status === 'pending' || item.status === 'running')
  useEffect(() => {
    if (!studyInProgress && !syncWait) return
    const timer = window.setInterval(() => {
      if (document.hidden) return
      void (async () => {
        try {
          if (studyInProgress) await loadStudies()
          if (syncWait) {
            const nextSync = await api<HolidaySync>('/api/tariff-lab/holidays/sync')
            setHolidaySync(nextSync)
            if (nextSync.last_run_at !== syncWait.lastRunAt) {
              setSyncWait(null)
              await loadStatus()
            } else if (Date.now() - syncWait.since > SYNC_WAIT_LIMIT_MS) {
              setSyncWait(null)
            }
          }
          setError('')
        } catch (err) { setError((err as Error).message) }
      })()
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [studyInProgress, syncWait, loadStudies, loadStatus])

  async function syncHolidays() {
    setBusy(true); setMessage(''); setError('')
    try {
      await api('/api/tariff-lab/holidays/sync', { method: 'POST' })
      setSyncWait({ lastRunAt: holidaySync?.last_run_at ?? null, since: Date.now() })
      setMessage('Sincronización de festivos en cola. Los datos se actualizarán en unos minutos.')
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function createStudy(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('')
    const form = new FormData(event.currentTarget)
    const tariffNames = form.getAll('tariff_name').map(String)
    const targetValue = String(form.get('target_occupancy') || '').trim()
    try {
      const created = await api<Study>('/api/tariff-lab/studies', {
        method: 'POST', body: JSON.stringify({
          name: form.get('name'), period_start: form.get('period_start'), period_end: form.get('period_end'),
          guests: Number(form.get('guests')), nights: Number(form.get('nights')),
          tariff_names: tariffNames,
          target_occupancy: targetValue ? Number(targetValue) / 100 : null,
        }),
      })
      setSelectedId(created.id); setSelectedStudy(created); setMessage('Estudio añadido a la cola de cálculo.')
      setStudies(await api<Study[]>('/api/tariff-lab/studies'))
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function downloadStudy(kind: 'tariffs.csv' | 'report.pdf') {
    if (!selectedStudy) return
    setDownloadError('')
    try {
      const extension = kind.endsWith('.csv') ? 'csv' : 'pdf'
      await apiDownload(`/api/tariff-lab/studies/${selectedStudy.id}/${kind}`, `propuesta-tarifas-${selectedStudy.id}.${extension}`)
    } catch (err) { setDownloadError((err as Error).message) }
  }

  if (loading) return <Card className="p-5 text-sm text-muted-foreground" role="status">Cargando el laboratorio de tarifas…</Card>

  const activeStudy = studies.find(item => item.status === 'pending' || item.status === 'running')
  const incompleteChecks = readiness?.checks.filter(item => !item.ready) || []
  const yearHolidays = holidays.filter(item => item.date.startsWith(holidayYear))

  return <div className="grid items-start gap-4">
    {error && <Alert variant="destructive" role="alert">{error}</Alert>}
    {message && <Alert variant="success" role="status">{message}</Alert>}
    <Card className="grid gap-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">PROPUESTAS REVISABLES</p>
          <h2 className="text-xl font-semibold">Preparación del laboratorio</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Las propuestas quedan como borradores. Solo una proyección de año aplicada por un administrador guarda temporadas nuevas en Tarifas; el laboratorio nunca publica cambios en Avantio.</p>
        </div>
        <Button type="button" variant="outline" onClick={() => void load()}>Actualizar estado</Button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {(readiness?.checks || []).map(check => <div key={check.key} className="flex items-center justify-between gap-3 rounded-md border border-border bg-background px-3 py-2.5 text-sm">
          <span>{checkLabels[check.key] || check.key}</span>
          <span className={check.ready ? 'text-success-ink' : 'text-warning-ink'}>{check.ready ? 'Listo' : 'Pendiente'}</span>
        </div>)}
      </div>
      <div className="grid gap-3 text-xs text-muted-foreground sm:grid-cols-2">
        {readiness?.scope && <p className="sm:col-span-2">Ámbito: {readiness.scope.label}</p>}
        <p>Histórico de reservas: {readiness?.reservation_coverage ? `${formatDate(readiness.reservation_coverage.from)} – ${formatDate(readiness.reservation_coverage.to)}` : 'sin datos'}</p>
        <p>Histórico de disponibilidad: {readiness?.availability_coverage ? `${formatDate(readiness.availability_coverage.from)} – ${formatDate(readiness.availability_coverage.to)}` : 'sin datos'}</p>
      </div>
      {incompleteChecks.some(item => item.key === 'market_provider') && <Alert variant="warning">El proveedor/API de mercado todavía no está instalado. Para activar la captura, hace falta integrar un proveedor autorizado con su contrato técnico.</Alert>}
      {!!readiness?.unmapped_properties.length && <Alert variant="warning">{readiness.unmapped_properties.length === 1 ? 'Hay 1 alojamiento' : `Hay ${readiness.unmapped_properties.length} alojamientos`} sin grupo SA deducible: quedan fuera de los estudios hasta que tengan reservas comparables.</Alert>}
      {!!readiness?.unmapped_groups.length && <Alert variant="warning">Hay grupos SA sin alojamientos vinculados: {readiness.unmapped_groups.join(', ')}.</Alert>}
      {readiness?.checks.some(item => item.key === 'availability_history' && !item.ready) && <Alert variant="warning">Sin calendario histórico de disponibilidad no se calcula una tasa de ocupación fiable.</Alert>}
    </Card>

    <TariffProjections admin={admin} groups={readiness?.groups || []} refreshVersion={refreshVersion} />

    <BookingPace refreshVersion={refreshVersion} />

    {admin && <Card className="grid gap-4 p-4 sm:p-6">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">NUEVO ESTUDIO</p>
        <h2 className="text-xl font-semibold">Generar propuesta SA</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">El cálculo usa una mediana del SA actual, ADR neto histórico y mercado comparable, con ajustes por ocupación histórica y un límite de ±{readiness?.price_change_limit_percent ?? 20}%.</p>
      </div>
      <form className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={event => void createStudy(event)}>
        <label className="grid gap-1.5 text-sm font-medium">Nombre del estudio<Input name="name" maxLength={120} placeholder="Ej. Temporada 2028" required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Desde<Input name="period_start" type="date" required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Hasta · inclusive<Input name="period_end" type="date" required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Huéspedes<Input name="guests" type="number" min={1} max={20} defaultValue={2} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Noches por búsqueda<Input name="nights" type="number" min={1} max={90} defaultValue={7} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Objetivo de ocupación · opcional (%)<Input name="target_occupancy" type="number" min={0} max={100} step={1} placeholder="Mediana histórica" /></label>
        <fieldset className="grid gap-2 sm:col-span-2 xl:col-span-2">
          <legend className="mb-1 text-sm font-medium">Grupos tarifarios</legend>
          <div className="flex flex-wrap gap-2">{(readiness?.groups || []).map(group => <label key={group} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"><input type="checkbox" name="tariff_name" value={group} defaultChecked />{group}</label>)}</div>
        </fieldset>
        <Button type="submit" className="w-fit" disabled={busy || !readiness?.can_generate}>{busy ? 'Enviando…' : activeStudy ? 'Añadir estudio' : 'Generar borrador →'}</Button>
      </form>
    </Card>}

    <div className="grid items-start gap-4 xl:grid-cols-[minmax(18rem,0.7fr)_minmax(0,1.3fr)]">
      <Card className="overflow-hidden">
        <div className="border-b border-border p-4"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">HISTORIAL</p><h2 className="text-lg font-semibold">Estudios guardados</h2></div>
        {!studies.length ? <p className="p-5 text-sm text-muted-foreground">Todavía no hay estudios guardados.</p> : <div className="divide-y divide-border">
          {studies.map(study => <button type="button" key={study.id} onClick={() => { setSelectedId(study.id); void api<Study>(`/api/tariff-lab/studies/${study.id}`).then(setSelectedStudy).catch(err => setError((err as Error).message)) }} className={`grid w-full gap-1 px-4 py-3 text-left hover:bg-accent ${study.id === selectedId ? 'bg-accent' : ''}`}>
            <span className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{study.name}</strong><span className="text-xs text-muted-foreground">{statusLabels[study.status] || study.status}</span></span>
            <small className="text-xs text-muted-foreground">{formatDate(study.period_start)} – {formatDate(study.period_end)} · {study.tariff_names.length} grupos</small>
            {study.error && <small className="text-xs text-error-ink">{study.error}</small>}
          </button>)}
        </div>}
      </Card>

      <Card className="grid min-w-0 gap-4 p-4 sm:p-6">
        {!selectedStudy ? <p className="text-sm text-muted-foreground">Selecciona un estudio para consultar sus resultados.</p> : <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">ESTUDIO #{selectedStudy.id} · {statusLabels[selectedStudy.status] || selectedStudy.status}</p>
              <h2 className="text-xl font-semibold">{selectedStudy.name}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{formatDate(selectedStudy.period_start)} – {formatDate(selectedStudy.period_end)} · {selectedStudy.guests} huéspedes · {selectedStudy.nights} noches</p>
            </div>
            {selectedStudy.result && <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" onClick={() => void downloadStudy('report.pdf')}>Descargar informe PDF</Button><Button type="button" onClick={() => void downloadStudy('tariffs.csv')} disabled={!selectedStudy.result.seasons.length}>Descargar propuesta CSV</Button></div>}
          </div>
          {downloadError && <Alert variant="destructive" role="alert">{downloadError}</Alert>}
          {selectedStudy.error && <Alert variant="warning">{selectedStudy.error}</Alert>}
          {(selectedStudy.status === 'pending' || selectedStudy.status === 'running') && <Alert variant="info" role="status">El worker procesa las consultas de mercado y los datos históricos. Esta vista se actualizará automáticamente.</Alert>}
          {selectedStudy.result && <>
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Días con propuesta</small><strong className="mt-1 block text-lg tabular-nums">{selectedStudy.result.generated_days}</strong></div>
              <div className="rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Días incompletos</small><strong className="mt-1 block text-lg tabular-nums">{selectedStudy.result.missing_days}</strong></div>
              <div className="rounded-md border border-border p-3"><small className="text-xs text-muted-foreground">Fuente de mercado</small><strong className="mt-1 block truncate text-sm">{selectedStudy.result.provider}</strong></div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {Object.entries(selectedStudy.result.history_coverage).map(([group, coverage]) => <div key={group} className="rounded-md border border-border p-3 text-xs">
                <strong className="block truncate text-foreground" title={group}>{group}</strong>
                <span className="mt-1 block text-muted-foreground">Años: {coverage.years.join(', ') || '—'}</span>
                <span className="block text-muted-foreground">Inventario completo: {coverage.days_with_inventory} / {coverage.days_expected} días</span>
                <span className="block text-muted-foreground">{coverage.sold_nights} noches vendidas / {coverage.sellable_nights} vendibles fiables</span>
                {!!coverage.uncovered_sold_nights && <span className="block text-warning-ink">{coverage.uncovered_sold_nights} ventas sin inventario completo</span>}
              </div>)}
            </div>
            <div className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm">
              <strong>Cómo se calculó</strong>
              {Object.entries(selectedStudy.result.method).map(([key, value]) => <p key={key} className="text-muted-foreground">{value}</p>)}
              <p className="text-xs text-muted-foreground">Captura: {new Date(selectedStudy.result.captured_at).toLocaleString('es-ES')} · versión {selectedStudy.algorithm_version}</p>
              <p className="text-xs text-muted-foreground">Fuentes: {selectedStudy.result.market_sources.join(', ') || 'sin observaciones'}</p>
              {!!selectedStudy.result.comparables.length && <div className="grid gap-1 border-t border-border pt-2"><strong className="text-xs">Alojamientos comparables</strong>{selectedStudy.result.comparables.slice(0, 30).map((item, index) => <span key={`${item.name}-${index}`} className="text-xs text-muted-foreground">{item.url ? <a className="underline underline-offset-2" href={item.url} target="_blank" rel="noreferrer">{item.name}</a> : item.name}</span>)}</div>}
            </div>
            <div><h3 className="mb-2 text-base font-semibold">Temporadas propuestas</h3>
              <div className="max-w-full overflow-auto rounded-lg border border-border"><Table><TableHeader><TableRow><TableHead>Grupo SA</TableHead><TableHead>Desde</TableHead><TableHead>Hasta</TableHead><TableHead className="text-right">€/noche</TableHead><TableHead className="text-right">Días</TableHead></TableRow></TableHeader>
                <TableBody>{selectedStudy.result.seasons.map((season, index) => <TableRow key={`${season.tariff_name}-${season.start_date}-${index}`}><TableCell className="min-w-52">{season.tariff_name}</TableCell><TableCell className="whitespace-nowrap">{formatDate(season.start_date)}</TableCell><TableCell className="whitespace-nowrap">{formatDate(season.end_date)}</TableCell><TableCell className="text-right font-medium tabular-nums">{money(season.nightly_rate)}</TableCell><TableCell className="text-right tabular-nums">{season.day_count}</TableCell></TableRow>)}
                  {!selectedStudy.result.seasons.length && <TableRow><TableCell colSpan={5} className="py-5 text-center text-sm text-muted-foreground">No hay periodos con datos suficientes para proponer precios.</TableCell></TableRow>}
                </TableBody>
              </Table></div>
            </div>
            <div><h3 className="mb-2 text-base font-semibold">Detalle diario</h3>
              <div className="max-h-[34rem] max-w-full overflow-auto rounded-lg border border-border"><Table><TableHeader><TableRow><TableHead>Fecha</TableHead><TableHead>Grupo</TableHead><TableHead className="text-right">SA actual</TableHead><TableHead className="text-right">ADR</TableHead><TableHead className="text-right">Ocupación</TableHead><TableHead className="text-right">Antelación · días</TableHead><TableHead className="text-right">Estancia · noches</TableHead><TableHead className="text-right">RevPAR</TableHead><TableHead className="text-right">Mercado</TableHead><TableHead className="text-right">Propuesta</TableHead><TableHead>Señal / confianza</TableHead></TableRow></TableHeader>
                <TableBody>{selectedStudy.result.daily.map((row, index) => <TableRow key={`${row.tariff_name}-${row.date}-${index}`}><TableCell className="whitespace-nowrap">{formatDate(row.date)}</TableCell><TableCell className="min-w-52">{row.tariff_name}</TableCell><TableCell className="text-right tabular-nums">{money(row.current_rate)}</TableCell><TableCell className="text-right tabular-nums">{money(row.historical_adr)}</TableCell><TableCell className="text-right tabular-nums">{percentage(row.historical_occupancy)}</TableCell><TableCell className="text-right tabular-nums">{row.lead_time_days == null ? '—' : Math.round(row.lead_time_days)}</TableCell><TableCell className="text-right tabular-nums">{row.length_of_stay == null ? '—' : Math.round(row.length_of_stay)}</TableCell><TableCell className="text-right tabular-nums">{money(row.historical_revpar)}</TableCell><TableCell className="text-right tabular-nums">{money(row.market_median)} <small className="text-muted-foreground">({row.market_sample_count})</small></TableCell><TableCell className="text-right font-medium tabular-nums">{money(row.recommended_rate)}</TableCell><TableCell className="min-w-44 text-xs text-muted-foreground">{row.reason ? gapLabels[row.reason] || row.reason : `${row.confidence}${!row.occupancy_reliable ? ' · ocupación sin inventario completo; ajuste omitido' : ''}${row.holiday_names.length ? ` · ${row.holiday_names.join(', ')}` : ''}`}</TableCell></TableRow>)}
                </TableBody>
              </Table></div>
            </div>
            {!!selectedStudy.result.warnings.length && <Alert variant="warning"><strong>Avisos de consulta</strong><ul className="mt-2 list-inside list-disc">{selectedStudy.result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></Alert>}
          </>}
        </>}
      </Card>
    </div>

    <Card className="p-0">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-6 [&::-webkit-details-marker]:hidden">
          <span><span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-primary">CALENDARIO OFICIAL</span>
            <span className="block text-base font-semibold">Festivos de {readiness?.scope.locality || 'Albir'}, {readiness?.scope.municipality || "l'Alfàs del Pi"}</span></span>
          <ChevronDown aria-hidden="true" className="size-5 shrink-0 text-muted-foreground motion-safe:transition-transform group-open:rotate-180" />
        </summary>
        <div className="grid gap-4 px-4 pb-4 sm:px-6 sm:pb-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="max-w-3xl text-sm leading-6 text-muted-foreground">Se obtienen cada día de fuentes oficiales: festivos nacionales y de la {readiness?.scope.autonomous_community || 'Comunitat Valenciana'} del BOE, y festivos locales del Ayuntamiento y del DOGV. No se editan a mano.</p>
            {admin && <Button type="button" variant="outline" disabled={busy} onClick={() => void syncHolidays()}>Sincronizar ahora</Button>}
          </div>
          {holidaySync && <div className="grid gap-3">
            <p className="text-xs text-muted-foreground">Última sincronización: {holidaySync.last_run_at ? new Date(holidaySync.last_run_at).toLocaleString('es-ES') : 'pendiente'}</p>
            {holidaySync.error && <Alert variant="destructive">No se pudo completar la última sincronización: {holidaySync.error}</Alert>}
            {!!holidaySync.warnings.length && <Alert variant="warning"><ul className="list-inside list-disc">{holidaySync.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></Alert>}
            <div className="flex flex-wrap gap-2">{holidaySync.coverage.map(item => <button type="button" key={item.year} onClick={() => setHolidayYear(String(item.year))} aria-pressed={holidayYear === String(item.year)} className={`grid min-w-28 gap-0.5 rounded-md border px-3 py-2 text-left text-xs ${holidayYear === String(item.year) ? 'border-primary bg-accent' : 'border-border bg-background hover:bg-accent'}`}>
              <strong className="text-sm text-foreground">{item.year}</strong>
              <span className="text-muted-foreground">{item.national + item.regional} nac. y aut.</span>
              <span className={item.local ? 'text-muted-foreground' : 'text-warning-ink'}>{item.local ? `${item.local} locales` : item.year < new Date().getFullYear() ? 'Locales sin fuente' : 'Locales pendientes'}</span>
            </button>)}</div>
          </div>}
          <div className="max-w-full overflow-auto rounded-lg border border-border">
            <Table><TableHeader><TableRow><TableHead>Fecha</TableHead><TableHead>Festivo</TableHead><TableHead>Ámbito</TableHead><TableHead>Fuente</TableHead></TableRow></TableHeader>
              <TableBody>{yearHolidays.map(item => <TableRow key={item.id}>
                <TableCell className="whitespace-nowrap">{formatDate(item.date)}</TableCell>
                <TableCell>{item.name}</TableCell>
                <TableCell>{scopeLabels[item.scope] || item.scope}</TableCell>
                <TableCell className="text-xs">{item.source_url ? <a className="underline underline-offset-2" href={item.source_url} target="_blank" rel="noreferrer">{sourceLabels[item.source] || item.source}{item.source_reference ? ` · ${item.source_reference}` : ''}</a> : sourceLabels[item.source] || item.source}</TableCell>
              </TableRow>)}
                {!yearHolidays.length && <TableRow><TableCell colSpan={4} className="py-6 text-center text-sm text-muted-foreground">No hay festivos oficiales de {holidayYear} todavía.</TableCell></TableRow>}
              </TableBody>
            </Table>
          </div>
        </div>
      </details>
    </Card>

    <Card className="p-0">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-lg p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-6 [&::-webkit-details-marker]:hidden">
          <span><span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-primary">VINCULACIÓN</span>
            <span className="block text-base font-semibold">Alojamientos por grupo SA</span></span>
          <ChevronDown aria-hidden="true" className="size-5 shrink-0 text-muted-foreground motion-safe:transition-transform group-open:rotate-180" />
        </summary>
        <div className="grid gap-4 px-4 pb-4 sm:px-6 sm:pb-6">
          <p className="text-sm leading-6 text-muted-foreground">Se deduce automáticamente: los alojamientos con el mismo edificio y capacidad toman el grupo cuyas tarifas SA, consultables en Tarifas, se ajustan mejor al precio de sus reservas confirmadas y pagadas. Si dos grupos quedan muy cerca, decide el nombre del edificio.</p>
          {!propertyGroups.length ? <p className="text-sm text-muted-foreground">Importa el catálogo de alojamientos y las reservas para deducir la vinculación.</p> :
            <div className="max-h-[28rem] max-w-full overflow-auto rounded-lg border border-border">
              <Table><TableHeader><TableRow><TableHead>Alojamiento</TableHead><TableHead>Grupo SA</TableHead><TableHead>Confianza</TableHead><TableHead>Motivo</TableHead></TableRow></TableHeader>
                <TableBody>{propertyGroups.map(item => <TableRow key={item.property_id}>
                  <TableCell className="min-w-44"><span className="block font-medium">{item.name}</span><small className="text-xs text-muted-foreground">{item.building || 'Sin edificio'} · {item.capacity} plazas</small></TableCell>
                  <TableCell className="min-w-52">{item.tariff_name || <span className="text-warning-ink">Sin grupo deducible</span>}</TableCell>
                  <TableCell>{item.confidence ? <Badge className={confidenceStyles[item.confidence]}>{item.confidence[0].toUpperCase() + item.confidence.slice(1)}</Badge> : '—'}</TableCell>
                  <TableCell className="min-w-72 text-xs leading-5 text-muted-foreground">{item.reason}</TableCell>
                </TableRow>)}</TableBody>
              </Table>
            </div>}
        </div>
      </details>
    </Card>
  </div>
}
