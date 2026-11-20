import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { CalendarRange, CircleCheck, CircleSlash, Undo2 } from 'lucide-react'
import { api } from './api'
import { stateLabel, stateStyle } from './Planning'
import type { IssueReservation, ReservationIssueKind, ReservationIssueReview, ReservationIssues as IssuesData } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

type Scope = 'current' | 'all'
const tabs: { id: ReservationIssueKind; label: string; hint: string }[] = [
  { id: 'overlap', label: 'Posibles dobles reservas', hint: 'Dos reservas que bloquean el mismo apartamento comparten noches.' },
  { id: 'amounts', label: 'Importes descuadrados', hint: 'En estancias vendidas, el total no coincide con neto + impuestos.' },
  { id: 'inactive', label: 'Apartamentos inactivos', hint: 'Reservas o consultas futuras en apartamentos marcados como inactivos en Avantio.' },
  { id: 'unknown', label: 'Apartamentos sin catálogo', hint: 'Reservas en apartamentos que no están en la última importación de alojamientos.' },
]
const DAY = 86_400_000
const money = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' })
const dayFormat = new Intl.DateTimeFormat('es-ES', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' })
const shortFormat = new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: '2-digit', timeZone: 'UTC' })
const time = (value: string) => Date.parse(`${value}T00:00:00Z`)
const fmt = (value: string) => dayFormat.format(new Date(time(value)))
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10)

export type PlanningTarget = { start: string; end: string; search: string }

function planningWindow(start: string, end: string): { start: string; end: string } {
  const from = time(start) - 7 * DAY
  const to = Math.min(time(end) + 7 * DAY, from + 92 * DAY)
  return { start: iso(from), end: iso(Math.max(to, from + 14 * DAY)) }
}

type TimelineBar = { key: string; label: string; start: string; end: string; status: string }

function Timeline({ bars, highlight, from, to }: { bars: TimelineBar[]; highlight?: { start: string; end: string }; from: number; to: number }) {
  const span = Math.max(DAY, to - from)
  const x = (ms: number) => Math.min(100, Math.max(0, ((ms - from) / span) * 100))
  const ticks = [from, from + span / 3, from + (2 * span) / 3, to]
  return <div className="grid gap-1" role="img" aria-label={bars.map(bar => `${bar.label}: ${fmt(bar.start)} a ${fmt(bar.end)}`).join('; ')}>
    <div className="relative rounded-md border border-border bg-background" style={{ height: bars.length * 34 + 12 }}>
      {highlight && <div aria-hidden="true" className="absolute inset-y-0 border-x-2 border-error-ink/60 bg-error-bg" style={{ left: `${x(time(highlight.start))}%`, width: `${Math.max(0.6, x(time(highlight.end)) - x(time(highlight.start)))}%` }} />}
      {bars.map((bar, index) => {
        const start = time(bar.start), end = time(bar.end)
        const before = start < from, after = end > to
        return <div key={bar.key} aria-hidden="true" title={`${bar.label} · ${fmt(bar.start)} → ${fmt(bar.end)}`}
          className={`absolute flex h-7 items-center overflow-hidden rounded px-2 text-[0.7rem] font-semibold whitespace-nowrap ${stateStyle[bar.status] || 'bg-secondary text-secondary-foreground'}`}
          style={{ top: 6 + index * 34, left: `${x(start)}%`, width: `${Math.max(1.5, x(end) - x(start))}%` }}>
          {before && '← '}{bar.label}{after && ' →'}
        </div>
      })}
    </div>
    <div className="flex justify-between text-[0.7rem] text-muted-foreground" aria-hidden="true">{ticks.map(tick => <span key={tick}>{shortFormat.format(new Date(tick))}</span>)}</div>
  </div>
}

function ReviewControl({ issueKey, review, busy, onReview, onUndo }: { issueKey: string; review: ReservationIssueReview | null; busy: boolean; onReview: (key: string, note: string) => void; onUndo: (key: string) => void }) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  if (review) return <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
    <CircleCheck size={14} className="text-success-ink" aria-hidden="true" />
    <span>Revisada{review.by ? ` por ${review.by}` : ''} el {new Date(review.at).toLocaleDateString('es-ES')}{review.note ? ` · «${review.note}»` : ''}</span>
    <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => onUndo(issueKey)}><Undo2 size={14} aria-hidden="true" /> Deshacer</Button>
  </div>
  if (!open) return <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setOpen(true)}><CircleCheck size={15} aria-hidden="true" /> Marcar como revisada</Button>
  return <form className="flex w-full flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); onReview(issueKey, note); setOpen(false); setNote('') }}>
    <label className="grid min-w-56 flex-1 gap-1 text-xs font-medium">Nota (opcional)<Input value={note} maxLength={300} onChange={event => setNote(event.target.value)} placeholder="Comprobado en Avantio: bloqueo de propietario" /></label>
    <Button type="submit" size="sm" disabled={busy}>Guardar</Button>
    <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
  </form>
}

function ReservationFacts({ reservation }: { reservation: IssueReservation }) {
  const rows: [string, string][] = [
    ['Estado', stateLabel[reservation.status] || reservation.status_original],
    ['Huésped', reservation.display_name || '—'],
    ['Canal', reservation.portal || '—'],
    ['Fechas', `${fmt(reservation.start_date)} → ${fmt(reservation.end_date)} · ${reservation.nights} noches`],
    ['Creada', reservation.created_on ? fmt(reservation.created_on) : '—'],
    ['Total', money.format(reservation.total)],
    ...(reservation.handled_by ? [['Atendido por', reservation.handled_by] as [string, string]] : []),
  ]
  return <div className="grid gap-2">
    <dl className="grid gap-1 text-sm">{rows.map(([label, value]) => <div key={label} className="grid grid-cols-[6.5rem_1fr] gap-2"><dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 break-words">{value}</dd></div>)}</dl>
    <AvantioComments text={reservation.comments} />
  </div>
}

function AvantioComments({ text, compact = false }: { text: string | null; compact?: boolean }) {
  if (!text) return null
  return <details className={`rounded-md bg-muted/60 text-xs ${compact ? 'mt-1 px-2 py-1' : 'px-3 py-2'}`}>
    <summary className="cursor-pointer font-medium text-muted-foreground">Comentarios en Avantio</summary>
    <p className="mt-1 whitespace-pre-line break-words leading-5">{text}</p>
  </details>
}

function HistoryBadge({ current }: { current: boolean }) {
  return current ? null : <Badge className="bg-muted text-muted-foreground">Histórico</Badge>
}

export function ReservationIssues({ onOpenPlanning, onChanged }: { onOpenPlanning: (target: PlanningTarget) => void; onChanged: () => void }) {
  const query = useMemo(() => new URLSearchParams(window.location.search), [])
  const [tab, setTab] = useState<ReservationIssueKind>(() => tabs.some(item => item.id === query.get('tab')) ? query.get('tab') as ReservationIssueKind : 'overlap')
  const [scope, setScope] = useState<Scope>('current')
  const [focusKey] = useState(query.get('issue'))
  const [data, setData] = useState<IssuesData | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setData(await api<IssuesData>('/api/reservation-issues')); setError('') }
    catch (err) { setError((err as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    const params = new URLSearchParams({ tab })
    window.history.replaceState({}, '', `/reservation-issues?${params}`)
  }, [tab])
  useEffect(() => {
    if (!data || !focusKey) return
    document.getElementById(`issue-${focusKey}`)?.scrollIntoView({ block: 'center' })
  }, [data, focusKey])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn(); await load(); onChanged() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const review = (key: string, note: string) => run(() => api('/api/reservation-issues/review', { method: 'POST', body: JSON.stringify({ key, note: note.trim() || null }) }))
  const undo = (key: string) => run(() => api('/api/reservation-issues/unreview', { method: 'POST', body: JSON.stringify({ key }) }))
  const reviewProps = { busy, onReview: review, onUndo: undo }
  const visible = <T extends { current: boolean }>(items: T[]) => items.filter(item => scope === 'all' || item.current)
  const ring = (key: string) => key === focusKey ? 'ring-2 ring-primary' : ''
  const visibleTabs = tabs.filter(item => item.id !== 'unknown' || (data?.counts.unknown.total || 0) > 0 || tab === 'unknown')
  const active = tabs.find(item => item.id === tab) || tabs[0]

  return <div className="grid gap-5">
    <Card className="grid gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">Incidencias detectadas en los datos de Avantio{data?.batch ? `, según la importación de reservas del ${new Date(data.batch.created_at).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' })}` : ''}. Corrígelas en Avantio o márcalas como revisadas si son correctas: dejan de contar en los avisos.</p>
        <label className="grid min-w-52 gap-1 text-xs font-semibold text-muted-foreground">Mostrar
          <Select value={scope} onChange={event => setScope(event.target.value as Scope)}>
            <option value="current">Vigentes y futuras</option>
            <option value="all">Todo, incluido histórico</option>
          </Select>
        </label>
      </div>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Tipos de incidencia">
        {visibleTabs.map(item => {
          const counts = data?.counts[item.id]
          const shown = counts ? (scope === 'all' ? counts.total : counts.current) : null
          return <Button key={item.id} type="button" role="tab" aria-selected={tab === item.id} aria-controls="issues-panel" variant={tab === item.id ? 'secondary' : 'ghost'} size="sm" onClick={() => setTab(item.id)}>
            {item.label}{shown !== null && <span className={`rounded-full px-1.5 text-[0.7rem] ${counts?.pending ? 'bg-warning-bg text-warning-ink' : 'bg-muted text-muted-foreground'}`}>{shown}</span>}
          </Button>
        })}
      </div>
    </Card>

    {error && <Alert variant="destructive">{error}</Alert>}

    <section id="issues-panel" role="tabpanel" aria-label={active.label} className="grid gap-4">
      <p className="text-sm text-muted-foreground">{active.hint}</p>
      {!data ? <div className="grid gap-3">{[0, 1].map(key => <Skeleton key={key} className="h-40 w-full rounded-xl" />)}</div>
        : tab === 'overlap' ? <OverlapTab items={visible(data.overlap)} ring={ring} onOpenPlanning={onOpenPlanning} {...reviewProps} />
        : tab === 'amounts' ? <AmountsTab items={visible(data.amounts)} ring={ring} {...reviewProps} />
        : <CatalogTab kind={tab} items={visible(data[tab])} ring={ring} {...reviewProps} />}
    </section>
  </div>
}

type ReviewProps = { busy: boolean; onReview: (key: string, note: string) => void; onUndo: (key: string) => void; ring: (key: string) => string }

function EmptyTab({ text }: { text: string }) {
  return <Card className="grid min-h-36 place-items-center gap-2 p-8 text-center"><CircleCheck className="text-success-ink" aria-hidden="true" /><p className="text-sm text-muted-foreground">{text}</p></Card>
}

function OverlapTab({ items, onOpenPlanning, ring, ...review }: ReviewProps & { items: IssuesData['overlap']; onOpenPlanning: (target: PlanningTarget) => void }) {
  if (!items.length) return <EmptyTab text="No hay reservas solapadas en este periodo." />
  return <ul className="grid gap-4">{items.map(item => {
    const pad = Math.min(60, Math.max(7, Math.round(item.nights * 0.4))) * DAY
    const from = time(item.overlap_start) - pad, to = time(item.overlap_end) + pad
    const range = planningWindow(item.overlap_start, item.overlap_end)
    return <li key={item.key} id={`issue-${item.key}`}>
      <Card className={`grid gap-4 p-4 sm:p-5 ${ring(item.key)}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold">{item.property.name || `Apartamento ${item.property.id}`}<span className="ml-2 text-xs font-normal text-muted-foreground">{item.property.building || item.property.id}</span></h2>
          <div className="flex flex-wrap gap-2"><HistoryBadge current={item.current} /><Badge className="bg-error-bg text-error-ink">Se solapan {item.nights} noches · {fmt(item.overlap_start)} – {fmt(item.overlap_end)}</Badge></div>
        </div>
        <Timeline from={from} to={to} highlight={{ start: item.overlap_start, end: item.overlap_end }} bars={item.reservations.map(reservation => ({ key: reservation.locator, label: `${reservation.locator} · ${stateLabel[reservation.status] || reservation.status_original}`, start: reservation.start_date, end: reservation.end_date, status: reservation.status }))} />
        <div className="grid gap-4 md:grid-cols-2">{item.reservations.map((reservation, index) => <div key={reservation.locator} className="grid gap-2 rounded-md border border-border p-3"><p className="text-xs font-semibold uppercase tracking-wide text-primary">Reserva {index ? 'B' : 'A'} · {reservation.locator}</p><ReservationFacts reservation={reservation} /></div>)}</div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => onOpenPlanning({ ...range, search: item.property.id })}><CalendarRange size={15} aria-hidden="true" /> Ver en el planning</Button>
          <ReviewControl issueKey={item.key} review={item.review} {...review} />
        </div>
      </Card>
    </li>
  })}</ul>
}

// Pending is total - paid on import, so only net + tax can disagree with the total.
function AmountsTab({ items, ring, ...review }: ReviewProps & { items: IssuesData['amounts'] }) {
  if (!items.length) return <EmptyTab text="No hay importes descuadrados en este periodo." />
  return <Card className="overflow-x-auto">
    <Table className="min-w-[52rem]">
      <TableHeader><TableRow><TableHead>Reserva</TableHead><TableHead>Apartamento</TableHead><TableHead>Fechas</TableHead><TableHead className="text-right">Total</TableHead><TableHead className="text-right">Neto</TableHead><TableHead className="text-right">Impuestos</TableHead><TableHead className="text-right">Diferencia</TableHead><TableHead>Revisión</TableHead></TableRow></TableHeader>
      <TableBody>{items.map(item => {
        const r = item.reservation
        const diff = item.tax_difference
        const context = r.comments || r.handled_by || r.portal
        return <Fragment key={item.key}><TableRow id={`issue-${item.key}`} className={`${ring(item.key)} ${context ? 'border-b-0' : ''}`}>
          <TableCell><strong className="block text-sm whitespace-nowrap">{r.locator}</strong><span className="text-xs text-muted-foreground">{r.display_name} · {stateLabel[r.status] || r.status_original}</span> <HistoryBadge current={item.current} /></TableCell>
          <TableCell className="text-sm">{item.property.name || r.property_id}</TableCell>
          <TableCell className="text-sm whitespace-nowrap">{fmt(r.start_date)} → {fmt(r.end_date)}</TableCell>
          <TableCell className="text-right tabular-nums">{money.format(r.total)}</TableCell>
          <TableCell className="text-right tabular-nums">{money.format(r.net)}</TableCell>
          <TableCell className="text-right tabular-nums">{money.format(r.tax)}</TableCell>
          <TableCell className="text-right tabular-nums"><span className="font-semibold text-warning-ink">{diff > 0 ? '+' : ''}{money.format(diff)}</span><span className="block text-[0.7rem] text-muted-foreground">neto + impuestos − total</span></TableCell>
          <TableCell><ReviewControl issueKey={item.key} review={item.review} {...review} /></TableCell>
        </TableRow>
        {context && <TableRow className={ring(item.key)}><TableCell colSpan={8} className="pt-0">
          <div className="flex flex-wrap items-start gap-x-4 gap-y-2 text-xs text-muted-foreground">
            {r.portal && <span>Canal: {r.portal}</span>}
            {r.handled_by && <span>Atendido por {r.handled_by}</span>}
            {r.comments && <div className="min-w-64 flex-1"><AvantioComments text={r.comments} compact /></div>}
          </div>
        </TableCell></TableRow>}
        </Fragment>
      })}</TableBody>
    </Table>
  </Card>
}

// No planning link here: the planning only lists active apartments of the catalogue.
function CatalogTab({ kind, items, ring, ...review }: ReviewProps & { kind: 'inactive' | 'unknown'; items: IssuesData['inactive'] }) {
  if (!items.length) return <EmptyTab text={kind === 'inactive' ? 'No hay reservas futuras en apartamentos inactivos.' : 'Todas las reservas están en apartamentos del catálogo.'} />
  const groups = new Map<string, IssuesData['inactive']>()
  items.forEach(item => groups.set(item.property.id, [...(groups.get(item.property.id) || []), item]))
  groups.forEach(list => list.sort((a, b) => a.reservation.start_date.localeCompare(b.reservation.start_date)))
  return <ul className="grid gap-4">{[...groups.entries()].map(([propertyId, list]) => {
    const property = list[0].property
    const from = Math.min(...list.map(item => time(item.reservation.start_date))) - 7 * DAY
    const to = Math.max(...list.map(item => time(item.reservation.end_date))) + 7 * DAY
    return <li key={propertyId}>
      <Card className="grid gap-4 p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold">{property.name || `Apartamento ${propertyId}`}<span className="ml-2 text-xs font-normal text-muted-foreground">{property.building || propertyId}{property.city ? ` · ${property.city}` : ''}</span></h2>
          <Badge className="gap-1 bg-warning-bg text-warning-ink"><CircleSlash size={12} aria-hidden="true" />{kind === 'inactive' ? 'Inactivo en Avantio' : 'No está en el catálogo'}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">{list.length === 1 ? 'Una reserva suelta suele indicar que está en el apartamento equivocado.' : `${list.length} reservas: si el apartamento sigue operativo, puede que se diera de baja por error.`}</p>
        {to - from <= 400 * DAY && <Timeline from={from} to={to} bars={list.map(item => ({ key: item.key, label: item.reservation.locator, start: item.reservation.start_date, end: item.reservation.end_date, status: item.reservation.status }))} />}
        <ul className="grid gap-3">{list.map(item => <li key={item.key} id={`issue-${item.key}`} className={`grid gap-2 rounded-md border border-border p-3 md:grid-cols-[1fr_auto] md:items-start ${ring(item.key)}`}>
          <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">{item.reservation.locator} <HistoryBadge current={item.current} /></p><ReservationFacts reservation={item.reservation} /></div>
          <ReviewControl issueKey={item.key} review={item.review} {...review} />
        </li>)}</ul>
      </Card>
    </li>
  })}</ul>
}
