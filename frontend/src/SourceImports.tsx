import { DragEvent, FormEvent, useState } from 'react'
import { api } from './api'
import type { ImportBatch, ImportDetail, ImportIssue, ImportPreview } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Select } from './components/ui/select'

const datasetName: Record<string, string> = { availability: 'Disponibilidad', properties: 'Propiedades', reservations: 'Reservas' }
const sourceName: Record<string, string> = { upload: 'Manual', folder: 'Carpeta vigilada', playwright: 'Playwright · Avantio' }
const issueName: Record<string, string> = {
  unknown_property: 'Alojamiento ausente del catálogo', inactive_property: 'Alojamiento inactivo',
  tax_mismatch: 'Total y tasas no cuadran', balance_mismatch: 'Total, pagado y pendiente no cuadran',
  overlap: 'Reservas bloqueantes solapadas',
}
export function issueText(issue: ImportIssue) {
  const reference = issue.locator || issue.locators?.join(' / ') || issue.property_id || ''
  return `${issueName[issue.code] || issue.code}${reference ? ` · ${reference}` : ''}`
}

export function SourceImportForm({ onDone, admin }: { onDone: () => Promise<void>; admin: boolean }) {
  const [dataset, setDataset] = useState<'properties' | 'reservations'>('properties')
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const file = files[0] || null
  function addFiles(selected: FileList | File[]) {
    const csvs = Array.from(selected).filter(candidate => candidate.name.toLowerCase().endsWith('.csv') || candidate.type === 'text/csv')
    setFiles(current => [...current, ...csvs.filter(candidate => !current.some(existing => existing.name === candidate.name && existing.size === candidate.size && existing.lastModified === candidate.lastModified))])
    setPreview(null); setMessage('')
  }
  function dropFiles(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false)
    if (admin && !busy) addFiles(event.dataTransfer.files)
  }
  async function validate(event: FormEvent) {
    event.preventDefault()
    if (!file) return
    setBusy(true); setMessage(''); setPreview(null)
    const form = new FormData(); form.set('file', file)
    try { setPreview(await api<ImportPreview>(`/api/source-imports/${dataset}/preview`, { method: 'POST', body: form })) }
    catch (err) { setMessage((err as Error).message) }
    finally { setBusy(false) }
  }
  async function commit() {
    if (!file || !preview) return
    setBusy(true); setMessage('')
    const form = new FormData(); form.set('file', file); form.set('expected_checksum', preview.checksum)
    try {
      const result = await api<{ id: number; status: string; error: string | null }>(`/api/source-imports/${dataset}`, { method: 'POST', body: form })
      setMessage(result.status === 'valid' ? `Instantánea #${result.id} activada.` : `Archivo rechazado: ${result.error}`)
      setPreview(null); setFiles(current => current.slice(1)); await onDone()
    } catch (err) { setMessage((err as Error).message) }
    finally { setBusy(false) }
  }
  return <form className="grid gap-4" onSubmit={validate}>
    <label className="grid gap-1.5 text-sm font-medium">Conjunto de datos<Select value={dataset} disabled={!admin || busy} onChange={e => { setDataset(e.target.value as 'properties' | 'reservations'); setPreview(null); setMessage('') }}><option value="properties">Propiedades</option><option value="reservations">Reservas</option></Select></label>
    <div className={`flex min-h-36 flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-5 text-center transition-colors ${dragging ? 'border-primary bg-accent' : 'border-border bg-background'}`} onDragEnter={event => { event.preventDefault(); setDragging(true) }} onDragOver={event => event.preventDefault()} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}>
      <strong className="text-sm">Suelta aquí tus archivos CSV</strong><span className="text-xs text-muted-foreground">o selecciónalos desde el dispositivo</span>
      <label className="mt-2 inline-flex min-h-10 cursor-pointer items-center rounded-md border border-border bg-card px-4 py-2 text-sm font-medium hover:bg-accent focus-within:ring-2 focus-within:ring-ring">Elegir archivos<input className="sr-only" type="file" accept=".csv,text/csv" multiple disabled={!admin || busy} onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} /></label>
    </div>
    {files.length > 0 && <div className="overflow-hidden rounded-lg border border-border" aria-label="Archivos pendientes de importación"><div className="flex items-center justify-between gap-3 bg-background px-3 py-2 text-sm"><strong>Cola de importación</strong><span className="text-xs text-muted-foreground">{files.length} {files.length === 1 ? 'archivo' : 'archivos'}</span></div>{files.map((queued, index) => <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2.5 text-sm" key={`${queued.name}-${queued.size}-${queued.lastModified}`}><span className="min-w-0 break-all"><strong>{queued.name}</strong><small className="mt-1 block text-xs text-muted-foreground">{(queued.size / 1024).toLocaleString('es-ES', { maximumFractionDigits: 0 })} KB{index === 0 ? ' · siguiente para revisar' : ''}</small></span>{index === 0 && !busy && <Button type="button" variant="link" size="sm" onClick={() => { setFiles(current => current.slice(1)); setPreview(null); setMessage('') }}>Quitar</Button>}</div>)}</div>}
    <Button variant="secondary" disabled={!admin || !file || busy}>{busy ? 'Validando…' : 'Ver vista previa'}</Button>
    {preview && <Card className="grid gap-3 bg-background p-4" role="status">
      <strong className="text-sm">{preview.rows} registros en el archivo · {preview.changes.length} cambios · {preview.warnings.length} incidencias</strong>
      {preview.coverage && <p className="text-sm leading-6 text-muted-foreground">Estancias presentes en este archivo: {preview.coverage.first_arrival} → {preview.coverage.last_departure}. Altas registradas: {preview.coverage.first_created || 'sin fecha'} → {preview.coverage.last_created || 'sin fecha'}. La fecha de alta no indica cuándo se ocupa el alojamiento.</p>}
      {preview.dataset === 'reservations' && <p className="text-sm leading-6 text-muted-foreground">Planning consolidado tras confirmar: {preview.effective_rows} reservas. Se conservarán {preview.carried_rows} reservas anteriores ausentes de este archivo; las coincidencias por localizador se actualizarán. Una ausencia no se interpreta como cancelación.</p>}
      {preview.dataset === 'properties' && preview.changes.some(change => change.kind === 'removed') && <Alert variant="warning">{preview.changes.filter(change => change.kind === 'removed').length} propiedades de la instantánea anterior faltan en este archivo.</Alert>}
      {preview.duplicate_of && <p className="text-sm">Archivo ya importado como instantánea #{preview.duplicate_of}.</p>}
      {preview.warnings.length > 0 && <ul className="max-h-56 list-disc space-y-1 overflow-auto pl-5 text-sm text-muted-foreground">{preview.warnings.slice(0, 12).map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul>}
      <small className="break-all text-xs text-muted-foreground">Hash SHA-256: {preview.checksum}</small>
      <Button type="button" className="w-fit" disabled={busy || !!preview.duplicate_of} onClick={() => void commit()}>Confirmar importación</Button>
    </Card>}
    {!admin && <p className="text-sm text-muted-foreground">Solo un administrador puede importar archivos.</p>}
    {message && <Alert variant={message.startsWith('Archivo rechazado:') ? 'destructive' : 'info'} role="status">{message}</Alert>}
  </form>
}

export function ImportHistory({ imports }: { imports: ImportBatch[] }) {
  const [detail, setDetail] = useState<ImportDetail | null>(null)
  const [error, setError] = useState('')
  async function open(id: number) {
    setError('')
    try { setDetail(await api<ImportDetail>(`/api/imports/${id}`)) }
    catch (err) { setError((err as Error).message) }
  }
  return <>
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">HISTORIAL</p><h2 className="mb-4 text-xl font-semibold">Instantáneas</h2>
    {imports.length ? <div className="grid overflow-hidden rounded-lg border border-border">{imports.map(item => <Button type="button" variant="ghost" className="h-auto min-h-20 justify-between gap-3 rounded-none border-b border-border p-3 text-left whitespace-normal last:border-b-0" key={item.id} onClick={() => void open(item.id)}><span className="grid min-w-0 gap-1"><strong className="text-sm">#{item.id} · {datasetName[item.dataset] || item.dataset}</strong><small className="break-all text-xs text-muted-foreground">{sourceName[item.source] || item.source} · {item.filename} · {new Date(item.created_at).toLocaleString('es-ES')} · {item.rows} filas</small><small className="text-xs text-muted-foreground">{item.change_count} cambios · {item.warning_count} incidencias</small>{item.error && <small className="text-xs text-error-ink">{item.error}</small>}</span><Badge className={item.status === 'valid' ? 'bg-success-bg text-success-ink' : 'bg-error-bg text-error-ink'}>{item.status === 'valid' ? 'Válida' : 'Inválida'}</Badge></Button>)}</div> : <p className="text-sm text-muted-foreground">Sin importaciones todavía.</p>}
    {error && <Alert variant="destructive" className="mt-4">{error}</Alert>}
    {detail && <Card className="mt-5 grid gap-3 bg-background p-4"><h3 className="text-lg font-semibold">Instantánea #{detail.id}</h3><p className="text-sm text-muted-foreground">Conjunto: {datasetName[detail.dataset] || detail.dataset} · origen: {sourceName[detail.source] || detail.source} · usuario #{detail.actor_user_id ?? '—'} · parser {detail.parser_version || 'anterior'}</p><p className="break-all text-sm">SHA-256: <code>{detail.checksum}</code></p><h3 className="text-base font-semibold">Incidencias ({detail.warnings.length})</h3>{detail.warnings.length ? <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{detail.warnings.map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul> : <p className="text-sm text-muted-foreground">Ninguna.</p>}<h3 className="text-base font-semibold">Cambios ({detail.changes.length})</h3>{detail.changes.length ? <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{detail.changes.map((change, index) => <li key={index}>{change.key} · {change.kind === 'added' ? 'alta' : change.kind === 'removed' ? 'ausente en la nueva instantánea' : `modificado: ${change.fields?.join(', ') || ''}`}</li>)}</ul> : <p className="text-sm text-muted-foreground">Ninguno.</p>}</Card>}
  </>
}
