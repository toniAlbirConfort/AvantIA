import { DragEvent, FormEvent, useState } from 'react'
import { api } from './api'
import type { ImportBatch, ImportDetail, ImportIssue, ImportPreview } from './types'
import { Button } from './components/ui/button'

const datasetName: Record<string, string> = { availability: 'Disponibilidad', properties: 'Propiedades', reservations: 'Reservas' }
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
  return <form className="stack" onSubmit={validate}>
    <label>Conjunto de datos<select value={dataset} disabled={!admin || busy} onChange={e => { setDataset(e.target.value as 'properties' | 'reservations'); setPreview(null); setMessage('') }}><option value="properties">Propiedades</option><option value="reservations">Reservas</option></select></label>
    <div className={`csv-dropzone ${dragging ? 'is-dragging' : ''}`} onDragEnter={event => { event.preventDefault(); setDragging(true) }} onDragOver={event => event.preventDefault()} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false) }} onDrop={dropFiles}>
      <strong>Suelta aquí tus archivos CSV</strong><span>o selecciónalos desde el dispositivo</span>
      <label className="csv-picker">Elegir archivos<input type="file" accept=".csv,text/csv" multiple disabled={!admin || busy} onChange={e => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} /></label>
    </div>
    {files.length > 0 && <div className="csv-queue" aria-label="Archivos pendientes de importación"><div className="csv-queue-heading"><strong>Cola de importación</strong><span>{files.length} {files.length === 1 ? 'archivo' : 'archivos'}</span></div>{files.map((queued, index) => <div className={`csv-queue-row ${index === 0 ? 'current' : ''}`} key={`${queued.name}-${queued.size}-${queued.lastModified}`}><span><strong>{queued.name}</strong><small>{(queued.size / 1024).toLocaleString('es-ES', { maximumFractionDigits: 0 })} KB{index === 0 ? ' · siguiente para revisar' : ''}</small></span>{index === 0 && !busy && <Button type="button" className="text-button" onClick={() => { setFiles(current => current.slice(1)); setPreview(null); setMessage('') }}>Quitar</Button>}</div>)}</div>}
    <Button className="button secondary" disabled={!admin || !file || busy}>{busy ? 'Validando…' : 'Ver vista previa'}</Button>
    {preview && <div className="import-preview" role="status">
      <strong>{preview.rows} registros en el archivo · {preview.changes.length} cambios · {preview.warnings.length} incidencias</strong>
      {preview.coverage && <p>Estancias presentes en este archivo: {preview.coverage.first_arrival} → {preview.coverage.last_departure}. Altas registradas: {preview.coverage.first_created || 'sin fecha'} → {preview.coverage.last_created || 'sin fecha'}. La fecha de alta no indica cuándo se ocupa el alojamiento.</p>}
      {preview.dataset === 'reservations' && <p>Planning consolidado tras confirmar: {preview.effective_rows} reservas. Se conservarán {preview.carried_rows} reservas anteriores ausentes de este archivo; las coincidencias por localizador se actualizarán. Una ausencia no se interpreta como cancelación.</p>}
      {preview.dataset === 'properties' && preview.changes.some(change => change.kind === 'removed') && <p className="error">{preview.changes.filter(change => change.kind === 'removed').length} propiedades de la instantánea anterior faltan en este archivo.</p>}
      {preview.duplicate_of && <p>Archivo ya importado como instantánea #{preview.duplicate_of}.</p>}
      {preview.warnings.length > 0 && <ul>{preview.warnings.slice(0, 12).map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul>}
      <small>Hash SHA-256: {preview.checksum}</small>
      <Button type="button" className="button primary" disabled={busy || !!preview.duplicate_of} onClick={() => void commit()}>Confirmar importación</Button>
    </div>}
    {!admin && <p className="muted">Solo un administrador puede importar archivos.</p>}
    {message && <p role="status">{message}</p>}
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
    <p className="eyebrow">HISTORIAL</p><h2>Instantáneas</h2>
    {imports.length ? imports.map(item => <Button type="button" className="import-row import-history-button" key={item.id} onClick={() => void open(item.id)}><span><strong>#{item.id} · {datasetName[item.dataset] || item.dataset}</strong><small>{item.filename} · {new Date(item.created_at).toLocaleString('es-ES')} · {item.rows} filas</small><small>{item.change_count} cambios · {item.warning_count} incidencias</small>{item.error && <small className="error">{item.error}</small>}</span><span className={`badge badge-${item.status}`}>{item.status === 'valid' ? 'Válida' : 'Inválida'}</span></Button>) : <p>Sin importaciones todavía.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {detail && <section className="import-detail"><h3>Instantánea #{detail.id}</h3><p>Fuente: {datasetName[detail.dataset] || detail.dataset} · usuario #{detail.actor_user_id ?? '—'} · parser {detail.parser_version || 'anterior'}</p><p>SHA-256: <code>{detail.checksum}</code></p><h3>Incidencias ({detail.warnings.length})</h3>{detail.warnings.length ? <ul>{detail.warnings.map((issue, index) => <li key={index}>{issueText(issue)}</li>)}</ul> : <p>Ninguna.</p>}<h3>Cambios ({detail.changes.length})</h3>{detail.changes.length ? <ul>{detail.changes.map((change, index) => <li key={index}>{change.key} · {change.kind === 'added' ? 'alta' : change.kind === 'removed' ? 'ausente en la nueva instantánea' : `modificado: ${change.fields?.join(', ') || ''}`}</li>)}</ul> : <p>Ninguno.</p>}</section>}
  </>
}
