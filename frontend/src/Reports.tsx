import { FormEvent, useCallback, useEffect, useState } from 'react'
import { ArrowLeft, BookOpen, Download, ExternalLink, FileText, FlaskConical, Plus, Sparkles, Trash2, X } from 'lucide-react'
import { api, apiDownload, apiText } from './api'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Skeleton } from './components/ui/skeleton'
import { Textarea } from './components/ui/textarea'

type ReportItem = {
  key: string; id: number; kind: 'document' | 'tariff_study'; source: 'claude' | 'app'
  title: string; description: string | null; created_at: string | null; created_by: string | null
  original_url?: string | null; status?: string
}
type SourceFilter = 'all' | 'claude' | 'app'

const sourceLabels: Record<ReportItem['source'], string> = { claude: 'Claude', app: 'AvantIA' }
const sourceTones: Record<ReportItem['source'], string> = {
  claude: 'bg-brand-blue-light text-brand-blue-ink',
  app: 'bg-brand-turquoise-light text-brand-turquoise-ink',
}

function dateLabel(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

function isToday(value: string | null) {
  if (!value) return false
  const day = new Date(value)
  const today = new Date()
  return day.getFullYear() === today.getFullYear() && day.getMonth() === today.getMonth() && day.getDate() === today.getDate()
}

function SourceBadge({ source }: { source: ReportItem['source'] }) {
  return <Badge className={sourceTones[source]}>{source === 'claude' ? <Sparkles size={12} aria-hidden="true" /> : <FlaskConical size={12} aria-hidden="true" />}{sourceLabels[source]}</Badge>
}

function UploadForm({ onDone, onCancel }: { onDone: (key: string) => void; onCancel: () => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [originalUrl, setOriginalUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!file) { setError('Elige el archivo HTML del informe.'); return }
    setBusy(true); setError('')
    const body = new FormData()
    body.set('file', file); body.set('title', title); body.set('source', 'claude')
    if (description.trim()) body.set('description', description)
    if (originalUrl.trim()) body.set('original_url', originalUrl)
    try {
      const created = await api<{ key: string }>('/api/reports', { method: 'POST', body })
      onDone(created.key)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  return <Card className="p-4 sm:p-5">
    <form className="grid gap-4" onSubmit={submit}>
      <div className="flex items-start justify-between gap-3">
        <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">NUEVO INFORME</p><h2 className="text-lg font-semibold">Añadir informe de Claude</h2></div>
        <Button type="button" variant="ghost" size="icon" onClick={onCancel} aria-label="Cerrar formulario"><X size={16} /></Button>
      </div>
      <label className="grid gap-1.5 text-sm font-medium">Título<Input id="report-title" value={title} onChange={event => setTitle(event.target.value)} maxLength={200} required /></label>
      <label className="grid gap-1.5 text-sm font-medium">Descripción<Textarea id="report-description" rows={2} value={description} onChange={event => setDescription(event.target.value)} /></label>
      <label className="grid gap-1.5 text-sm font-medium">Archivo HTML<Input id="report-file" type="file" accept=".html,.htm,text/html" onChange={event => setFile(event.target.files?.[0] || null)} required /></label>
      <label className="grid gap-1.5 text-sm font-medium">Enlace original (opcional)<Input id="report-url" type="url" placeholder="https://claude.ai/artifact/…" value={originalUrl} onChange={event => setOriginalUrl(event.target.value)} /></label>
      {error && <Alert variant="destructive" role="alert">{error}</Alert>}
      <div className="flex flex-wrap gap-2"><Button disabled={busy}>{busy ? 'Guardando…' : 'Guardar informe'}</Button><Button type="button" variant="outline" onClick={onCancel}>Cancelar</Button></div>
    </form>
  </Card>
}

function DocumentViewer({ item, frameClassName = 'h-[calc(100vh-17rem)] min-h-[32rem]' }: { item: ReportItem; frameClassName?: string }) {
  const [html, setHtml] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setHtml(null); setError('')
    apiText(`/api/reports/${item.id}/content`)
      .then(result => { if (active) setHtml(result) })
      .catch(err => { if (active) setError((err as Error).message) })
    return () => { active = false }
  }, [item.id])
  if (error) return <Alert variant="destructive" className="m-4">{error}</Alert>
  if (html === null) return <div className="grid gap-3 p-6"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-4 w-full" /><Skeleton className="h-64 w-full" /></div>
  return <iframe title={item.title} srcDoc={html} sandbox="allow-popups allow-popups-to-escape-sandbox" className={`${frameClassName} w-full border-0 bg-white`} />
}

function ReadingMode({ item, onClose }: { item: ReportItem; onClose: () => void }) {
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.getElementById('report-reading-close')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); onClose() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      previousFocus?.focus()
    }
  }, [onClose])

  return <div role="dialog" aria-modal="true" aria-label={`Lectura: ${item.title}`} className="fixed inset-0 z-50 flex flex-col bg-background">
    <header className="flex shrink-0 items-center gap-3 border-b border-border bg-card px-3 py-2 sm:px-5">
      <Button id="report-reading-close" variant="outline" size="sm" onClick={onClose}><ArrowLeft size={16} /> Volver a Informes</Button>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold">{item.title}</p>
        <p className="hidden truncate text-xs text-muted-foreground sm:block">{sourceLabels[item.source]} · {dateLabel(item.created_at)} · Esc para salir</p>
      </div>
    </header>
    <div className="min-h-0 flex-1">
      <DocumentViewer item={item} frameClassName="h-full" />
    </div>
  </div>
}

function StudySummary({ item, onOpenTariffLab }: { item: ReportItem; onOpenTariffLab: () => void }) {
  const [error, setError] = useState('')
  async function downloadPdf() {
    setError('')
    try { await apiDownload(`/api/tariff-lab/studies/${item.id}/report.pdf`, `estudio-tarifas-${item.id}.pdf`) }
    catch (err) { setError((err as Error).message) }
  }
  return <div className="grid gap-4 p-4 sm:p-6">
    <p className="max-w-2xl text-sm leading-6 text-muted-foreground">Este informe lo genera el Laboratorio de tarifas. Descarga el PDF o abre el estudio para ver el detalle diario y la propuesta de temporadas.</p>
    {item.status === 'partial' && <Alert variant="warning">El estudio terminó con datos parciales. Revisa los huecos antes de usar la propuesta.</Alert>}
    {error && <Alert variant="destructive" role="alert">{error}</Alert>}
    <div className="flex flex-wrap gap-2"><Button type="button" onClick={() => void downloadPdf()}><Download size={16} /> Descargar PDF</Button><Button type="button" variant="outline" onClick={onOpenTariffLab}><FlaskConical size={16} /> Abrir en el laboratorio</Button></div>
  </div>
}

export function Reports({ role, refreshVersion, onOpenTariffLab }: { role: string; refreshVersion: number; onOpenTariffLab: () => void }) {
  const [items, setItems] = useState<ReportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<SourceFilter>('all')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [showDetail, setShowDetail] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [reading, setReading] = useState<ReportItem | null>(null)
  const closeReading = useCallback(() => setReading(null), [])
  const admin = role === 'admin'

  const load = useCallback(async (focusKey?: string) => {
    setLoading(true); setError('')
    try {
      const result = await api<ReportItem[]>('/api/reports')
      setItems(result)
      setSelectedKey(current => focusKey || (current && result.some(item => item.key === current) ? current : result[0]?.key || null))
    } catch (err) { setError((err as Error).message) } finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load, refreshVersion])

  const visible = items.filter(item => filter === 'all' || item.source === filter)
  const selected = items.find(item => item.key === selectedKey) || null

  async function remove(item: ReportItem) {
    setError('')
    try { await api(`/api/reports/${item.id}`, { method: 'DELETE' }); setShowDetail(false); await load() }
    catch (err) { setError((err as Error).message) }
  }

  return <div className="grid gap-4">
    {reading && <ReadingMode item={reading} onClose={closeReading} />}
    {error && <Alert variant="destructive" role="alert">{error}</Alert>}
    {uploading && <UploadForm onCancel={() => setUploading(false)} onDone={key => { setUploading(false); setShowDetail(true); void load(key) }} />}
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(20rem,4fr)_minmax(0,8fr)]">
      <section className={`min-w-0 overflow-hidden rounded-xl border border-border bg-card ${showDetail ? 'hidden lg:block' : ''}`} aria-label="Listado de informes">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
          <div><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">{visible.length} {visible.length === 1 ? 'INFORME' : 'INFORMES'}</p><h2 className="text-lg font-semibold">Más recientes primero</h2></div>
          {admin && !uploading && <Button size="sm" variant="secondary" onClick={() => setUploading(true)}><Plus size={15} /> Añadir</Button>}
        </div>
        <div className="flex flex-wrap gap-1 border-b border-border p-3" role="group" aria-label="Filtrar por origen">
          {([['all', 'Todos'], ['claude', 'Claude'], ['app', 'AvantIA']] as [SourceFilter, string][]).map(([value, caption]) => <Button key={value} size="sm" variant={filter === value ? 'secondary' : 'ghost'} aria-pressed={filter === value} onClick={() => setFilter(value)}>{caption}</Button>)}
        </div>
        <div className="max-h-[calc(100vh-20rem)] min-h-64 overflow-y-auto">
          {loading && !items.length
            ? <div className="grid gap-3 p-4"><Skeleton className="h-16 w-full" /><Skeleton className="h-16 w-full" /></div>
            : visible.length
              ? visible.map(item => <button key={item.key} type="button" onClick={() => { setSelectedKey(item.key); setShowDetail(true); if (item.kind === 'document') setReading(item) }} aria-current={selectedKey === item.key ? 'true' : undefined} className={`grid w-full gap-1.5 border-b border-border px-4 py-3.5 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selectedKey === item.key ? 'border-l-4 border-l-primary bg-accent pl-3' : ''}`}>
                <span className="flex items-center justify-between gap-3"><SourceBadge source={item.source} /><small className="shrink-0 text-xs text-muted-foreground">{isToday(item.created_at) ? <strong className="font-semibold text-primary">Hoy</strong> : dateLabel(item.created_at)}</small></span>
                <strong className="text-sm leading-5">{item.title}</strong>
                {item.description && <span className="line-clamp-2 text-sm leading-5 text-muted-foreground">{item.description}</span>}
              </button>)
              : <div className="grid min-h-40 place-items-center px-6 py-10 text-center text-sm leading-7 text-muted-foreground">{items.length ? 'No hay informes de este origen.' : 'Todavía no hay informes. Los estudios del laboratorio y los informes de Claude aparecerán aquí.'}</div>}
        </div>
      </section>
      <section className={`min-w-0 overflow-hidden rounded-xl border border-border bg-card ${showDetail ? '' : 'hidden lg:block'}`} aria-label="Informe seleccionado">
        {selected ? <>
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4 sm:px-6">
            <div className="min-w-0">
              <Button variant="outline" size="sm" className="mb-3 lg:hidden" onClick={() => setShowDetail(false)}><ArrowLeft size={16} /> Informes</Button>
              <div className="mb-2 flex flex-wrap items-center gap-2"><SourceBadge source={selected.source} /><span className="text-xs text-muted-foreground">{dateLabel(selected.created_at)}{selected.created_by ? ` · ${selected.created_by}` : ''}</span></div>
              <h2 className="text-xl font-semibold leading-tight">{selected.title}</h2>
              {selected.description && <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">{selected.description}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              {selected.kind === 'document' && <Button size="sm" onClick={() => setReading(selected)}><BookOpen size={15} /> Modo lectura</Button>}
              {selected.original_url && <Button asChild variant="outline" size="sm"><a href={selected.original_url} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Original</a></Button>}
              {admin && selected.kind === 'document' && <Button variant="ghost" size="sm" onClick={() => { if (window.confirm(`¿Borrar el informe «${selected.title}»? No se puede deshacer.`)) void remove(selected) }}><Trash2 size={15} /> Borrar</Button>}
            </div>
          </div>
          {selected.kind === 'document' ? <DocumentViewer item={selected} /> : <StudySummary item={selected} onOpenTariffLab={onOpenTariffLab} />}
        </> : <div className="grid min-h-80 place-items-center gap-2 px-6 py-10 text-center text-sm text-muted-foreground"><FileText size={28} aria-hidden="true" />Selecciona un informe para leerlo.</div>}
      </section>
    </div>
  </div>
}
