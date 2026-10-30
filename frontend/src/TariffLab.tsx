import { useCallback, useEffect, useState } from 'react'
import { api, token } from './api'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

type ReadinessCheck = { key: string; ready: boolean; count?: number; name?: string | null; unmapped?: { id: string; name: string; city: string | null }[] }
type Readiness = {
  can_generate: boolean; groups: string[]; active_properties: number
  unmapped_properties: { id: string; name: string; city: string | null }[]
  unmapped_groups: string[]
  reservation_coverage: { from: string; to: string } | null
  availability_coverage: { from: string; to: string } | null
  checks: ReadinessCheck[]; algorithm_version: string; price_change_limit_percent: number
}
type PropertyMapping = {
  property_id: string; name: string; city: string | null; capacity: number
  tariff_name: string; municipality: string; province: string; autonomous_community: string
}
type Holiday = { id: number; date: string; name: string; scope: string; region: string | null; municipality: string | null; source: string }
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

const apiBase = import.meta.env.VITE_API_BASE || ''
const checkLabels: Record<string, string> = {
  properties: 'Catálogo activo', group_mapping: 'Asignación a grupos SA', reservations: 'Histórico de reservas',
  availability_history: 'Histórico de disponibilidad', market_provider: 'Proveedor de mercado autorizado',
  holiday_calendar: 'Calendario de festivos',
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

function percentage(value: number | null | undefined) {
  return value == null ? '—' : new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 0 }).format(value)
}

async function download(path: string, filename: string) {
  const headers = new Headers()
  if (token()) headers.set('Authorization', `Bearer ${token()}`)
  const response = await fetch(`${apiBase}${path}`, { headers })
  if (!response.ok) {
    let message = `Error ${response.status}`
    try { message = (await response.json()).detail || message } catch { /* use status */ }
    throw new Error(message)
  }
  const objectUrl = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  link.click()
  URL.revokeObjectURL(objectUrl)
}

export function TariffLab({ role, refreshVersion }: { role: string; refreshVersion: number }) {
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [mappings, setMappings] = useState<PropertyMapping[]>([])
  const [holidays, setHolidays] = useState<Holiday[]>([])
  const [studies, setStudies] = useState<Study[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedStudy, setSelectedStudy] = useState<Study | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingMapping, setSavingMapping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const admin = role === 'admin'

  const load = useCallback(async () => {
    try {
      const [nextReadiness, nextMappings, nextHolidays, nextStudies] = await Promise.all([
        api<Readiness>('/api/tariff-lab/readiness'),
        api<PropertyMapping[]>('/api/tariff-lab/property-groups'),
        api<Holiday[]>('/api/tariff-lab/holidays'),
        api<Study[]>('/api/tariff-lab/studies'),
      ])
      setReadiness(nextReadiness)
      setMappings(nextMappings)
      setHolidays(nextHolidays)
      setStudies(nextStudies)
      if (selectedId != null) {
        const selected = await api<Study>(`/api/tariff-lab/studies/${selectedId}`)
        setSelectedStudy(selected)
      } else if (nextStudies.length) {
        setSelectedId(nextStudies[0].id)
        setSelectedStudy(await api<Study>(`/api/tariff-lab/studies/${nextStudies[0].id}`))
      }
      setError('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [selectedId])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 12000)
    return () => window.clearInterval(timer)
  }, [load, refreshVersion])

  const updateMapping = (propertyId: string, field: keyof PropertyMapping, value: string) => {
    setMappings(current => current.map(item => item.property_id === propertyId ? { ...item, [field]: value } : item))
  }

  async function saveMappings() {
    setSavingMapping(true); setMessage(''); setError('')
    try {
      const items = mappings.filter(item => item.tariff_name).map(item => ({
        property_id: item.property_id, tariff_name: item.tariff_name, municipality: item.municipality,
        province: item.province || null, autonomous_community: item.autonomous_community || null,
      }))
      const saved = await api<PropertyMapping[]>('/api/tariff-lab/property-groups', {
        method: 'PUT', body: JSON.stringify({ items }),
      })
      setMappings(saved); setMessage('Asignaciones guardadas.')
      await load()
    } catch (err) { setError((err as Error).message) }
    finally { setSavingMapping(false) }
  }

  async function createHoliday(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setMessage(''); setError('')
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const scope = String(form.get('scope'))
    try {
      await api('/api/tariff-lab/holidays', { method: 'POST', body: JSON.stringify({
        holiday_date: form.get('date'), name: form.get('name'), scope,
        region: scope === 'regional' ? form.get('region') : null,
        municipality: scope === 'municipal' ? form.get('municipality') : null,
        source: form.get('source') || 'manual',
      }) })
      formElement.reset(); setMessage('Festivo añadido.'); await load()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function deleteHoliday(id: number) {
    setBusy(true); setError('')
    try { await api(`/api/tariff-lab/holidays/${id}`, { method: 'DELETE' }); await load() }
    catch (err) { setError((err as Error).message) }
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
      await load()
    } catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  async function downloadStudy(kind: 'tariffs.csv' | 'report.pdf') {
    if (!selectedStudy) return
    setDownloadError('')
    try {
      const extension = kind.endsWith('.csv') ? 'csv' : 'pdf'
      await download(`/api/tariff-lab/studies/${selectedStudy.id}/${kind}`, `propuesta-tarifas-${selectedStudy.id}.${extension}`)
    } catch (err) { setDownloadError((err as Error).message) }
  }

  if (loading) return <Card className="p-5 text-sm text-muted-foreground" role="status">Cargando el laboratorio de tarifas…</Card>

  const activeStudy = studies.find(item => item.status === 'pending' || item.status === 'running')
  const incompleteChecks = readiness?.checks.filter(item => !item.ready) || []

  return <div className="grid items-start gap-4">
    {error && <Alert variant="destructive" role="alert">{error}</Alert>}
    {message && <Alert variant="success" role="status">{message}</Alert>}
    <Card className="grid gap-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">PROPUESTAS REVISABLES</p>
          <h2 className="text-xl font-semibold">Preparación del laboratorio</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Las propuestas quedan como borradores descargables. El laboratorio no actualiza las tarifas vigentes ni publica cambios en Avantio.</p>
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
        <p>Histórico de reservas: {readiness?.reservation_coverage ? `${formatDate(readiness.reservation_coverage.from)} – ${formatDate(readiness.reservation_coverage.to)}` : 'sin datos'}</p>
        <p>Histórico de disponibilidad: {readiness?.availability_coverage ? `${formatDate(readiness.availability_coverage.from)} – ${formatDate(readiness.availability_coverage.to)}` : 'sin datos'}</p>
      </div>
      {incompleteChecks.some(item => item.key === 'market_provider') && <Alert variant="warning">El proveedor/API de mercado todavía no está instalado. Para activar la captura, hace falta integrar un proveedor autorizado con su contrato técnico.</Alert>}
      {!!readiness?.unmapped_properties.length && <Alert variant="warning">Asigna los {readiness.unmapped_properties.length} alojamientos activos a un grupo SA antes de generar un estudio.</Alert>}
      {!!readiness?.unmapped_groups.length && <Alert variant="warning">Hay grupos SA sin alojamientos asignados: {readiness.unmapped_groups.join(', ')}.</Alert>}
      {readiness?.checks.some(item => item.key === 'availability_history' && !item.ready) && <Alert variant="warning">Sin calendario histórico de disponibilidad no se calcula una tasa de ocupación fiable.</Alert>}
    </Card>

    {admin && <Card className="grid gap-4 p-4 sm:p-6">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CATÁLOGO</p>
        <h2 className="text-xl font-semibold">Grupos SA y ubicación</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">Vincula cada alojamiento con su grupo de tarifa y completa el municipio y la comunidad autónoma para comparar alojamientos equivalentes y resolver festivos.</p>
      </div>
      {!mappings.length ? <p className="text-sm text-muted-foreground">Importa el catálogo de alojamientos para configurar la asignación.</p> : <>
        <div className="max-w-full overflow-auto rounded-lg border border-border">
          <Table><TableHeader><TableRow><TableHead>Alojamiento</TableHead><TableHead>Grupo SA</TableHead><TableHead>Municipio</TableHead><TableHead>Provincia</TableHead><TableHead>Comunidad autónoma</TableHead></TableRow></TableHeader>
            <TableBody>{mappings.map(item => <TableRow key={item.property_id}>
              <TableCell className="min-w-48"><span className="block font-medium">{item.name}</span><small className="text-xs text-muted-foreground">{item.property_id} · {item.capacity} plazas</small></TableCell>
              <TableCell className="min-w-64"><Select aria-label={`Grupo de tarifa para ${item.name}`} value={item.tariff_name} onChange={event => updateMapping(item.property_id, 'tariff_name', event.target.value)}><option value="">Selecciona un grupo</option>{(readiness?.groups || []).map(group => <option key={group} value={group}>{group}</option>)}</Select></TableCell>
              <TableCell className="min-w-36"><Input aria-label={`Municipio de ${item.name}`} value={item.municipality} onChange={event => updateMapping(item.property_id, 'municipality', event.target.value)} /></TableCell>
              <TableCell className="min-w-36"><Input aria-label={`Provincia de ${item.name}`} value={item.province} onChange={event => updateMapping(item.property_id, 'province', event.target.value)} /></TableCell>
              <TableCell className="min-w-44"><Input aria-label={`Comunidad autónoma de ${item.name}`} value={item.autonomous_community} onChange={event => updateMapping(item.property_id, 'autonomous_community', event.target.value)} /></TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </div>
        <Button type="button" className="w-fit" disabled={savingMapping} onClick={() => void saveMappings()}>{savingMapping ? 'Guardando…' : 'Guardar asignaciones'}</Button>
      </>}
    </Card>}

    {admin && <Card className="grid gap-4 p-4 sm:p-6">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CALENDARIO</p>
        <h2 className="text-xl font-semibold">Festivos por ámbito</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">Añade fechas nacionales, autonómicas o municipales. Se relacionan con la ubicación guardada en cada alojamiento.</p>
      </div>
      <form className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-6" onSubmit={event => void createHoliday(event)}>
        <label className="grid gap-1.5 text-sm font-medium">Fecha<Input name="date" type="date" required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Nombre<Input name="name" maxLength={180} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Ámbito<Select name="scope" defaultValue="national"><option value="national">Nacional</option><option value="regional">Autonómico</option><option value="municipal">Municipal</option></Select></label>
        <label className="grid gap-1.5 text-sm font-medium">Comunidad autónoma<Input name="region" placeholder="Requerida en ámbito autonómico" /></label>
        <label className="grid gap-1.5 text-sm font-medium">Municipio<Input name="municipality" placeholder="Requerido en ámbito municipal" /></label>
        <Button type="submit" disabled={busy}>Añadir festivo</Button>
      </form>
      <div className="max-w-full overflow-auto rounded-lg border border-border">
        <Table><TableHeader><TableRow><TableHead>Fecha</TableHead><TableHead>Festivo</TableHead><TableHead>Ámbito</TableHead><TableHead>Ubicación</TableHead><TableHead className="text-right">Acción</TableHead></TableRow></TableHeader>
          <TableBody>{holidays.map(item => <TableRow key={item.id}><TableCell className="whitespace-nowrap">{formatDate(item.date)}</TableCell><TableCell>{item.name}</TableCell><TableCell>{item.scope === 'regional' ? 'Autonómico' : item.scope === 'municipal' ? 'Municipal' : 'Nacional'}</TableCell><TableCell>{item.municipality || item.region || 'España'}</TableCell><TableCell className="text-right"><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void deleteHoliday(item.id)}>Eliminar</Button></TableCell></TableRow>)}
            {!holidays.length && <TableRow><TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">Todavía no hay festivos cargados.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </div>
    </Card>}

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
  </div>
}
