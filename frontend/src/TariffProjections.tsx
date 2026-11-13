import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiDownload } from './api'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'
import { Textarea } from './components/ui/textarea'

type Observation = {
  id: number; tariff_name: string; competitor: string; unit_label: string | null; stay_date: string
  nights: number; guests: number; nightly_rate: string; source: string; observed_on: string; notes: string | null
}
type ProjectionSeason = {
  key: string; tariff_name: string; base_start: string; base_end: string; start: string; end: string
  base_rate: string; occupancy: number | null; occupancy_year: number | null; previous_occupancy: number | null
  previous_year: number | null; adr: string | null; market_median: string | null; market_refs: number
  market_gap: number | null; cost_adjustment: number; demand_adjustment: number; market_adjustment: number
  total_change: number; rate: string; monthly_occupancy: Record<string, number> | null; flags: string[]
}
type FinalSeason = {
  key: string; tariff_name: string; start: string; end: string; proposed_rate: string; rate: string
  override_reason: string | null; note: string | null
}
type GroupSummary = {
  units: number; average_base_rate: string | null; average_rate: string | null
  pickup: { year: number; nights: number; previous_nights: number; change: number | null } | null
}
type ProjectionResult = {
  as_of: string; parameters: Record<string, number | string>; groups: Record<string, GroupSummary>
  seasons: ProjectionSeason[]; warnings: string[]
  easter: { base_easter: string; target_easter: string; window_start: string; window_end: string; groups: Record<string, { easter_season: { start: string; end: string } }> }
}
type Projection = {
  id: number; name: string; base_year: number; target_year: number; tariff_names: string[]
  status: 'draft' | 'applied'; algorithm_version: string; move_easter: boolean; notes: string | null
  created_at: string | null; applied_at: string | null; override_count: number
  result?: ProjectionResult; overrides?: Record<string, { rate: string; reason: string | null }>
  final_seasons?: FinalSeason[]
}
type Draft = Record<string, { rate: string; reason: string }>

const flagLabels: Record<string, string> = {
  separar_temporada: 'Meses muy distintos: valora separarla',
  mercado_pocas_referencias: 'Pocas referencias de mercado: sin ajuste',
  limitado: 'Ajustada al límite de subida',
  sin_historico: 'Sin histórico de ocupación',
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function shortDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString('es-ES', { day: 'numeric', month: 'short' })
}

function money(value: string | null | undefined) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(Number(value))
}

function percent(value: number | null | undefined, signed = false) {
  if (value == null) return '—'
  return new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 1, signDisplay: signed ? 'exceptZero' : 'auto' }).format(value)
}

function Adjustment({ value }: { value: number }) {
  if (!value) return <span className="text-muted-foreground">0</span>
  return <span className={value > 0 ? 'text-success-ink' : 'text-warning-ink'}>{percent(value, true)}</span>
}

export function TariffProjections({ admin, groups, refreshVersion }: { admin: boolean; groups: string[]; refreshVersion: number }) {
  const [projections, setProjections] = useState<Projection[]>([])
  const [observations, setObservations] = useState<Observation[]>([])
  const [selected, setSelected] = useState<Projection | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [notes, setNotes] = useState('')
  const [confirmApply, setConfirmApply] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const year = new Date().getFullYear()

  const select = useCallback(async (id: number) => {
    const projection = await api<Projection>(`/api/tariff-lab/projections/${id}`)
    setSelected(projection)
    setDraft(Object.fromEntries((projection.final_seasons || []).map(item => [item.key, { rate: String(Math.round(Number(item.rate))), reason: item.override_reason || '' }])))
    setNotes(projection.notes || '')
    setConfirmApply(false)
  }, [])

  const load = useCallback(async () => {
    try {
      const [nextProjections, nextObservations] = await Promise.all([
        api<Projection[]>('/api/tariff-lab/projections'),
        api<Observation[]>('/api/tariff-lab/market-observations'),
      ])
      setProjections(nextProjections)
      setObservations(nextObservations)
      setError('')
      return nextProjections
    } catch (err) {
      setError((err as Error).message)
      return []
    }
  }, [])

  const initialized = useRef(false)
  useEffect(() => {
    void load().then(items => {
      if (!initialized.current && items.length) void select(items[0].id).catch(err => setError((err as Error).message))
      initialized.current = true
    })
  }, [load, select, refreshVersion])

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true); setError(''); setMessage('')
    try { await action(); setMessage(success) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }

  function number(form: FormData, key: string) {
    const value = String(form.get(key) ?? '').trim()
    return value === '' ? null : Number(value)
  }

  async function createProjection(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    await run(async () => {
      const created = await api<Projection>('/api/tariff-lab/projections', {
        method: 'POST', body: JSON.stringify({
          name: form.get('name'), base_year: Number(form.get('base_year')), target_year: Number(form.get('target_year')),
          tariff_names: form.getAll('projection_group').map(String),
          parameters: {
            cost_percent: number(form, 'cost_percent'), market_inflation_percent: number(form, 'market_inflation_percent'),
            min_change_percent: number(form, 'min_change_percent'), max_change_percent: number(form, 'max_change_percent'),
            min_units: number(form, 'min_units'), rounding: form.get('rounding'),
          },
        }),
      })
      await load(); await select(created.id)
    }, 'Proyección calculada. Revisa cada temporada antes de aplicarla.')
  }

  async function addObservation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    await run(async () => {
      await api('/api/tariff-lab/market-observations', {
        method: 'POST', body: JSON.stringify({
          tariff_names: form.getAll('observation_group').map(String), competitor: form.get('competitor'),
          unit_label: form.get('unit_label') || null, stay_date: form.get('stay_date'), nights: Number(form.get('nights')),
          guests: Number(form.get('guests')), nightly_rate: Number(form.get('nightly_rate')),
          observed_on: form.get('observed_on'), source: form.get('source') || 'manual',
        }),
      })
      formElement.reset(); await load()
    }, 'Precio de competencia guardado.')
  }

  async function deleteObservation(id: number) {
    await run(async () => { await api(`/api/tariff-lab/market-observations/${id}`, { method: 'DELETE' }); await load() }, 'Precio eliminado.')
  }

  function pendingOverrides() {
    const overrides: Record<string, { rate: number; reason: string | null } | null> = {}
    // Keep manual prices of seasons hidden by the Easter toggle.
    for (const [key, value] of Object.entries(selected?.overrides || {})) overrides[key] = { rate: Number(value.rate), reason: value.reason }
    for (const item of selected?.final_seasons || []) {
      const edit = draft[item.key]
      if (!edit) continue
      const rate = Number(edit.rate)
      if (edit.rate.trim() && Number.isFinite(rate) && rate > 0 && rate !== Number(item.proposed_rate)) overrides[item.key] = { rate, reason: edit.reason.trim() || null }
      else delete overrides[item.key]
    }
    return overrides
  }

  async function patch(projection: Projection, extra: { move_easter?: boolean } = {}) {
    return api<Projection>(`/api/tariff-lab/projections/${projection.id}`, {
      method: 'PATCH', body: JSON.stringify({ overrides: pendingOverrides(), notes, ...extra }),
    })
  }

  async function saveChanges(moveEaster?: boolean) {
    if (!selected) return
    await run(async () => {
      await patch(selected, moveEaster == null ? {} : { move_easter: moveEaster })
      await load(); await select(selected.id)
    }, moveEaster == null ? 'Cambios guardados.' : moveEaster ? `Semana Santa se aplica en sus fechas de ${selected.target_year}.` : 'Se mantienen las fechas del año base.')
  }

  async function applyProjection() {
    if (!selected) return
    await run(async () => {
      await patch(selected)
      await api(`/api/tariff-lab/projections/${selected.id}/apply`, { method: 'POST' })
      await load(); await select(selected.id)
    }, `Tarifas SA ${selected.target_year} guardadas en Tarifas. Avantio no se ha modificado.`)
  }

  async function deleteProjection() {
    if (!selected) return
    await run(async () => {
      await api(`/api/tariff-lab/projections/${selected.id}`, { method: 'DELETE' })
      setSelected(null)
      const items = await load()
      if (items.length) await select(items[0].id)
    }, 'Proyección eliminada.')
  }

  const seasonsByGroup = useMemo(() => {
    const result: Record<string, ProjectionSeason[]> = {}
    for (const item of selected?.result?.seasons || []) (result[item.tariff_name] ||= []).push(item)
    return result
  }, [selected])
  const finalByGroup = useMemo(() => {
    const result: Record<string, FinalSeason[]> = {}
    for (const item of selected?.final_seasons || []) (result[item.tariff_name] ||= []).push(item)
    return result
  }, [selected])

  const editable = admin && selected?.status === 'draft'
  const easter = selected?.result?.easter
  const easterMoves = !!easter && Object.keys(easter.groups).length > 0

  return <div className="grid items-start gap-4">
    {error && <Alert variant="destructive" role="alert">{error}</Alert>}
    {message && <Alert variant="success" role="status">{message}</Alert>}

    {admin && <Card className="grid gap-4 p-4 sm:p-6">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">PROYECCIÓN DE AÑO</p>
        <h2 className="text-xl font-semibold">Proyectar las tarifas SA de un año a otro</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Parte de las temporadas SA del año base con las mismas fechas y ajusta cada precio por costes (IPC), ocupación propia y precios de la competencia. No necesita proveedor de mercado. Revisa el resultado y aplícalo a Tarifas cuando esté listo. Avantio no se modifica.</p>
      </div>
      <form className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-4" onSubmit={event => void createProjection(event)}>
        <label className="grid gap-1.5 text-sm font-medium">Nombre<Input name="name" maxLength={120} defaultValue={`Tarifas SA ${year + 2}`} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Año base<Input name="base_year" type="number" min={2000} max={2100} defaultValue={year + 1} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Año a proyectar<Input name="target_year" type="number" min={2001} max={2101} defaultValue={year + 2} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Redondeo<Select name="rounding" defaultValue="euro"><option value="euro">Al euro</option><option value="nine">Acabado en 9</option></Select></label>
        <label className="grid gap-1.5 text-sm font-medium">IPC y costes (%)<Input name="cost_percent" type="number" step={0.1} min={-10} max={25} defaultValue={3.5} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">IPC para precios de mercado antiguos (%)<Input name="market_inflation_percent" type="number" step={0.1} min={-10} max={25} defaultValue={3.7} /></label>
        <label className="grid gap-1.5 text-sm font-medium">Subida mínima (%)<Input name="min_change_percent" type="number" step={0.5} min={-25} max={25} defaultValue={0} /></label>
        <label className="grid gap-1.5 text-sm font-medium">Subida máxima (%)<Input name="max_change_percent" type="number" step={0.5} min={-25} max={50} defaultValue={10} /></label>
        <label className="grid gap-1.5 text-sm font-medium">Mínimo de alojamientos para usar la ocupación<Input name="min_units" type="number" min={1} max={100} defaultValue={3} /></label>
        <fieldset className="grid gap-2 sm:col-span-2 xl:col-span-3">
          <legend className="mb-1 text-sm font-medium">Grupos SA</legend>
          <div className="flex flex-wrap gap-2">{groups.map(group => <label key={group} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"><input type="checkbox" name="projection_group" value={group} defaultChecked />{group}</label>)}</div>
        </fieldset>
        <div className="grid gap-1 text-xs leading-5 text-muted-foreground sm:col-span-2 xl:col-span-4">
          <p><strong className="text-foreground">Demanda:</strong> +2 puntos si la temporada llegó al 80 % de ocupación; −2 si quedó por debajo del 60 % y cayó 5 puntos o más frente al año anterior.</p>
          <p><strong className="text-foreground">Mercado:</strong> con 2 o más competidores, si estamos un 10 % o más por debajo de la mediana se suma un tercio de la diferencia (máximo 5 puntos); si estamos un 10 % o más por encima, −2 puntos.</p>
        </div>
        <Button type="submit" className="w-fit" disabled={busy || !groups.length}>{busy ? 'Calculando…' : 'Calcular proyección'}</Button>
      </form>
    </Card>}

    <Card className="grid gap-4 p-4 sm:p-6">
      <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">MERCADO</p>
        <h2 className="text-xl font-semibold">Precios de la competencia</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Precios consultados a mano (por ejemplo en Booking). Cada precio se compara con la temporada en la que cae su fecha de entrada. Los de años anteriores se actualizan con el IPC indicado en la proyección.</p>
      </div>
      {admin && <form className="grid items-end gap-3 sm:grid-cols-2 xl:grid-cols-5" onSubmit={event => void addObservation(event)}>
        <label className="grid gap-1.5 text-sm font-medium">Competidor<Input name="competitor" maxLength={180} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Unidad<Input name="unit_label" maxLength={120} placeholder="Ej. 2 dorm." /></label>
        <label className="grid gap-1.5 text-sm font-medium">Entrada<Input name="stay_date" type="date" required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Noches<Input name="nights" type="number" min={1} max={90} defaultValue={7} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Huéspedes<Input name="guests" type="number" min={1} max={20} defaultValue={2} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">€ por noche<Input name="nightly_rate" type="number" min={1} max={10000} step={0.01} required /></label>
        <label className="grid gap-1.5 text-sm font-medium">Consultado el<Input name="observed_on" type="date" required /></label>
        <label className="grid gap-1.5 text-sm font-medium sm:col-span-2 xl:col-span-3">Fuente<Input name="source" maxLength={255} placeholder="Booking.com" /></label>
        <fieldset className="grid gap-2 sm:col-span-2 xl:col-span-4">
          <legend className="mb-1 text-sm font-medium">Comparar con</legend>
          <div className="flex flex-wrap gap-2">{groups.map(group => <label key={group} className="inline-flex min-h-10 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"><input type="checkbox" name="observation_group" value={group} />{group}</label>)}</div>
        </fieldset>
        <Button type="submit" className="w-fit" disabled={busy}>Añadir precio</Button>
      </form>}
      <div className="max-h-[24rem] max-w-full overflow-auto rounded-lg border border-border">
        <Table><TableHeader><TableRow><TableHead>Grupo SA</TableHead><TableHead>Competidor</TableHead><TableHead>Entrada</TableHead><TableHead className="text-right">Noches</TableHead><TableHead className="text-right">€/noche</TableHead><TableHead>Consulta</TableHead>{admin && <TableHead className="text-right">Acción</TableHead>}</TableRow></TableHeader>
          <TableBody>{observations.map(item => <TableRow key={item.id}>
            <TableCell className="min-w-48">{item.tariff_name}</TableCell>
            <TableCell className="min-w-44">{item.competitor}{item.unit_label && <small className="block text-xs text-muted-foreground">{item.unit_label}</small>}</TableCell>
            <TableCell className="whitespace-nowrap">{formatDate(item.stay_date)}</TableCell>
            <TableCell className="text-right tabular-nums">{item.nights}</TableCell>
            <TableCell className="text-right font-medium tabular-nums">{money(item.nightly_rate)}</TableCell>
            <TableCell className="min-w-40 text-xs text-muted-foreground">{formatDate(item.observed_on)} · {item.source}</TableCell>
            {admin && <TableCell className="text-right"><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void deleteObservation(item.id)}>Eliminar</Button></TableCell>}
          </TableRow>)}
            {!observations.length && <TableRow><TableCell colSpan={admin ? 7 : 6} className="py-6 text-center text-sm text-muted-foreground">Todavía no hay precios de la competencia.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </div>
    </Card>

    <div className="grid items-start gap-4 xl:grid-cols-[minmax(16rem,0.5fr)_minmax(0,1.5fr)]">
      <Card className="overflow-hidden">
        <div className="border-b border-border p-4"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">PROYECCIONES</p><h2 className="text-lg font-semibold">Guardadas</h2></div>
        {!projections.length ? <p className="p-5 text-sm text-muted-foreground">Todavía no hay proyecciones.</p> : <div className="divide-y divide-border">
          {projections.map(item => <button type="button" key={item.id} onClick={() => void select(item.id).catch(err => setError((err as Error).message))} className={`grid w-full gap-1 px-4 py-3 text-left hover:bg-accent ${item.id === selected?.id ? 'bg-accent' : ''}`}>
            <span className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{item.name}</strong><Badge className={item.status === 'applied' ? 'bg-success-bg text-success-ink' : 'bg-secondary text-secondary-foreground'}>{item.status === 'applied' ? 'Aplicada' : 'Borrador'}</Badge></span>
            <small className="text-xs text-muted-foreground">{item.base_year} → {item.target_year} · {item.tariff_names.length} grupos{item.override_count ? ` · ${item.override_count} cambios manuales` : ''}</small>
          </button>)}
        </div>}
      </Card>

      <Card className="grid min-w-0 gap-5 p-4 sm:p-6">
        {!selected?.result ? <p className="text-sm text-muted-foreground">Selecciona o calcula una proyección para revisarla.</p> : <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">PROYECCIÓN #{selected.id} · {selected.status === 'applied' ? 'APLICADA' : 'BORRADOR'}</p>
              <h2 className="text-xl font-semibold">{selected.name}</h2>
              <p className="mt-1 text-sm text-muted-foreground">SA {selected.base_year} → SA {selected.target_year} · reservas hasta el {formatDate(selected.result.as_of)} · IPC y costes {percent(Number(selected.result.parameters.cost_percent) / 100)}</p>
              {selected.applied_at && <p className="mt-1 text-sm text-success-ink">Aplicada a Tarifas el {new Date(selected.applied_at).toLocaleString('es-ES')}.</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => void apiDownload(`/api/tariff-lab/projections/${selected.id}/tariffs.csv`, `tarifas-sa-${selected.target_year}.csv`).catch(err => setError((err as Error).message))}>Descargar CSV</Button>
              {editable && <Button type="button" variant="ghost" disabled={busy} onClick={() => void deleteProjection()}>Eliminar</Button>}
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(selected.result.groups).map(([group, summary]) => {
              const average = summary.average_base_rate && summary.average_rate ? Number(summary.average_rate) / Number(summary.average_base_rate) - 1 : null
              return <div key={group} className="grid gap-1 rounded-md border border-border p-3 text-xs">
                <strong className="truncate text-sm text-foreground" title={group}>{group}</strong>
                <span className="text-muted-foreground">Media {money(summary.average_base_rate)} → <strong className="text-foreground">{money(summary.average_rate)}</strong> ({percent(average, true)})</span>
                <span className="text-muted-foreground">{summary.units} alojamientos asignados</span>
                {summary.pickup && <span className={summary.pickup.change != null && summary.pickup.change < -0.15 ? 'text-warning-ink' : 'text-muted-foreground'}>Reservado para {summary.pickup.year}: {summary.pickup.nights} noches frente a {summary.pickup.previous_nights} hace un año{summary.pickup.change != null ? ` (${percent(summary.pickup.change, true)})` : ''}</span>}
              </div>
            })}
          </div>

          {!!selected.result.warnings.length && <Alert variant="warning"><ul className="list-inside list-disc">{selected.result.warnings.map(item => <li key={item}>{item}</li>)}</ul></Alert>}

          {easterMoves && easter && <div className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm">
            <strong>Semana Santa cambia de fechas</strong>
            <p className="text-muted-foreground">En {selected.base_year} el Domingo de Pascua es el {shortDate(easter.base_easter)} y en {selected.target_year} el {shortDate(easter.target_easter)}. Si se mueve, del {shortDate(easter.window_start)} al {shortDate(easter.window_end)} se aplica el precio de Semana Santa, y su antigua temporada toma el precio de la anterior.</p>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={selected.move_easter} disabled={!editable || busy} onChange={event => void saveChanges(event.target.checked)} />Mover el precio de Semana Santa a sus fechas de {selected.target_year}</label>
          </div>}

          <div className="grid gap-2">
            <h3 className="text-base font-semibold">Cálculo por temporada</h3>
            <p className="text-xs text-muted-foreground">Ocupación y precio real: misma temporada del año indicado, con IVA. Mercado: mediana de la competencia y cuánto está por encima (+) o por debajo (−) de nuestra SA.</p>
            {Object.entries(seasonsByGroup).map(([group, rows]) => <div key={group} className="grid gap-1.5">
              <h4 className="text-sm font-semibold">{group}</h4>
              <div className="max-w-full overflow-auto rounded-lg border border-border"><Table>
                <TableHeader><TableRow><TableHead>Temporada {selected.target_year}</TableHead><TableHead className="text-right">SA {selected.base_year}</TableHead><TableHead className="text-right">Ocupación</TableHead><TableHead className="text-right">Precio real</TableHead><TableHead className="text-right">Mercado</TableHead><TableHead className="text-right">Costes</TableHead><TableHead className="text-right">Demanda</TableHead><TableHead className="text-right">Mercado</TableHead><TableHead className="text-right">Total</TableHead><TableHead className="text-right">Propuesta</TableHead><TableHead>Avisos</TableHead></TableRow></TableHeader>
                <TableBody>{rows.map(row => <TableRow key={row.key}>
                  <TableCell className="whitespace-nowrap">{shortDate(row.start)} – {shortDate(row.end)}</TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.base_rate)}</TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums">{percent(row.occupancy)}<small className="block text-xs text-muted-foreground">{row.occupancy_year ?? '—'}{row.previous_occupancy != null ? ` · antes ${percent(row.previous_occupancy)}` : ''}</small></TableCell>
                  <TableCell className="text-right tabular-nums">{money(row.adr)}</TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums">{money(row.market_median)}{row.market_refs > 0 && <small className="block text-xs text-muted-foreground">{row.market_refs} ref. · {percent(row.market_gap, true)}</small>}</TableCell>
                  <TableCell className="text-right tabular-nums"><Adjustment value={row.cost_adjustment} /></TableCell>
                  <TableCell className="text-right tabular-nums"><Adjustment value={row.demand_adjustment} /></TableCell>
                  <TableCell className="text-right tabular-nums"><Adjustment value={row.market_adjustment} /></TableCell>
                  <TableCell className="text-right font-medium tabular-nums">{percent(row.total_change, true)}</TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">{money(row.rate)}</TableCell>
                  <TableCell className="min-w-48 text-xs text-muted-foreground">{row.flags.map(flag => flagLabels[flag] || flag).join(' · ')}{row.monthly_occupancy && <span className="block">{Object.entries(row.monthly_occupancy).map(([month, value]) => `${month.slice(5)}: ${percent(value)}`).join(' · ')}</span>}</TableCell>
                </TableRow>)}</TableBody>
              </Table></div>
            </div>)}
          </div>

          <div className="grid gap-2">
            <h3 className="text-base font-semibold">Temporadas que se guardarán en Tarifas</h3>
            <p className="text-xs text-muted-foreground">{editable ? 'Puedes cambiar cualquier precio y anotar el motivo. Los cambios se guardan con la proyección.' : 'Precios finales de la proyección.'}</p>
            {Object.entries(finalByGroup).map(([group, rows]) => <div key={group} className="grid gap-1.5">
              <h4 className="text-sm font-semibold">{group}</h4>
              <div className="max-w-full overflow-auto rounded-lg border border-border"><Table>
                <TableHeader><TableRow><TableHead>Desde</TableHead><TableHead>Hasta</TableHead><TableHead className="text-right">Propuesta</TableHead><TableHead className="text-right">€/noche</TableHead><TableHead>Motivo del cambio</TableHead></TableRow></TableHeader>
                <TableBody>{rows.map(row => <TableRow key={row.key}>
                  <TableCell className="whitespace-nowrap">{formatDate(row.start)}{row.note && <small className="block text-xs text-muted-foreground">{row.note === 'Semana Santa' ? 'Semana Santa' : 'Sin Semana Santa'}</small>}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDate(row.end)}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{money(row.proposed_rate)}</TableCell>
                  <TableCell className="w-32 text-right">{editable ? <Input aria-label={`Precio de ${group} desde ${formatDate(row.start)}`} className="text-right tabular-nums" type="number" min={1} max={10000} step={1} value={draft[row.key]?.rate ?? ''} onChange={event => setDraft(current => ({ ...current, [row.key]: { rate: event.target.value, reason: current[row.key]?.reason ?? '' } }))} /> : <strong className="tabular-nums">{money(row.rate)}</strong>}</TableCell>
                  <TableCell className="min-w-56">{editable ? <Input aria-label={`Motivo del cambio de ${group} desde ${formatDate(row.start)}`} maxLength={300} value={draft[row.key]?.reason ?? ''} onChange={event => setDraft(current => ({ ...current, [row.key]: { rate: current[row.key]?.rate ?? '', reason: event.target.value } }))} /> : <span className="text-xs text-muted-foreground">{row.override_reason || ''}</span>}</TableCell>
                </TableRow>)}</TableBody>
              </Table></div>
            </div>)}
          </div>

          {editable ? <label className="grid gap-1.5 text-sm font-medium">Notas de la revisión<Textarea value={notes} maxLength={4000} onChange={event => setNotes(event.target.value)} /></label>
            : selected.notes && <div className="grid gap-1 text-sm"><strong>Notas de la revisión</strong><p className="whitespace-pre-wrap text-muted-foreground">{selected.notes}</p></div>}

          {editable && <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button type="button" variant="outline" disabled={busy} onClick={() => void saveChanges()}>Guardar cambios</Button>
            {!confirmApply ? <Button type="button" disabled={busy} onClick={() => setConfirmApply(true)}>Aplicar a Tarifas…</Button> : <>
              <span className="text-sm">Se guardarán {selected.final_seasons?.length} temporadas SA de {selected.target_year} en Tarifas, con los cambios de esta pantalla. Después la proyección no se podrá editar.</span>
              <Button type="button" disabled={busy} onClick={() => void applyProjection()}>Confirmar y aplicar</Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={() => setConfirmApply(false)}>Cancelar</Button>
            </>}
          </div>}
        </>}
      </Card>
    </div>
  </div>
}
