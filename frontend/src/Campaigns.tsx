import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Eye, Mail, Monitor, RefreshCw, RotateCcw, Save, Search, Send, Smartphone, Trash2, UserMinus, UserPlus } from 'lucide-react'
import { api } from './api'
import type { CampaignContent, CampaignContentFields, CampaignDetail, CampaignLanguage, CampaignOffer, CampaignPreview, CampaignRecipient, CampaignSummary } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'
import { Textarea } from './components/ui/textarea'

const integer = new Intl.NumberFormat('es-ES', { useGrouping: 'always' } as unknown as Intl.NumberFormatOptions)
const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
const CONTACT_LABELS = { any: '', email: 'con email', phone: 'con teléfono', opt_in: 'que aceptan campañas' }
const FIELDS: { key: keyof CampaignContentFields; label: string; hint?: string; multiline?: boolean }[] = [
  { key: 'subject', label: 'Asunto' },
  { key: 'preheader', label: 'Texto de vista previa', hint: 'Lo que el cliente ve junto al asunto en la bandeja de entrada.' },
  { key: 'headline', label: 'Titular' },
  { key: 'body', label: 'Mensaje', hint: 'El saludo con el nombre se añade solo. Separa los párrafos con una línea en blanco.', multiline: true },
  { key: 'offer', label: 'Oferta destacada', hint: 'Aparece en el recuadro junto al código y la fecha límite.' },
  { key: 'cta_label', label: 'Texto del botón' },
]
const RECIPIENTS_PAGE = 50

function formatDate(value: string | null | undefined, withTime = false) {
  if (!value) return '—'
  const options: Intl.DateTimeFormatOptions = withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }
  return new Intl.DateTimeFormat('es-ES', options).format(new Date(withTime ? value : `${value.slice(0, 10)}T12:00:00Z`))
}

function yearList(years: number[]) {
  if (years.length < 2) return years.join('')
  return `${years.slice(0, -1).join(', ')} y ${years[years.length - 1]}`
}

export function filtersText(filters: CampaignDetail['filters']) {
  const months = filters.month_from && filters.month_to
    ? filters.month_from === filters.month_to ? ` en ${MONTHS[filters.month_from - 1]}` : ` entre ${MONTHS[filters.month_from - 1]} y ${MONTHS[filters.month_to - 1]}`
    : ''
  const parts = [`Se alojaron${months} en ${yearList(filters.base_years)} y no tienen reserva para ${filters.target_year}`]
  if (filters.min_stays > 1) parts.push(`${filters.min_stays} o más estancias`)
  if (filters.channel) parts.push(`canal habitual ${filters.channel}`)
  if (filters.contact !== 'any') parts.push(CONTACT_LABELS[filters.contact])
  if (filters.q) parts.push(`nombre con «${filters.q}»`)
  return parts.join(' · ')
}

function Metric({ label, value, detail, tone }: { label: string; value: number; detail: string; tone?: string }) {
  return <Card className="p-4">
    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
    <strong className={`mt-1 block text-3xl font-semibold tabular-nums ${tone || ''}`}>{integer.format(value)}</strong>
    <small className="text-xs text-muted-foreground">{detail}</small>
  </Card>
}

export function Campaigns({ onRetention }: { onRetention: () => void }) {
  const [campaignId, setCampaignId] = useState<number | null>(() => Number(window.location.pathname.split('/')[2]) || null)

  useEffect(() => {
    const onPopState = () => setCampaignId(Number(window.location.pathname.split('/')[2]) || null)
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const open = useCallback((id: number | null) => {
    window.history.pushState({}, '', id ? `/campaigns/${id}` : '/campaigns')
    setCampaignId(id)
    window.scrollTo({ top: 0 })
  }, [])

  return campaignId
    ? <CampaignEditor key={campaignId} id={campaignId} onBack={() => open(null)} />
    : <CampaignList onOpen={open} onRetention={onRetention} />
}

function DemoNotice() {
  return <Alert variant="info" role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1">
    <strong className="text-xs font-semibold uppercase tracking-wide">Envío desactivado</strong>
    <span>AvantIA prepara la campaña, su audiencia y cada email tal como lo recibiría el cliente. Todavía no se envía ningún correo: el envío se activará al conectar el proveedor de email.</span>
  </Alert>
}

function CampaignList({ onOpen, onRetention }: { onOpen: (id: number) => void; onRetention: () => void }) {
  const [items, setItems] = useState<CampaignSummary[] | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setError('')
    api<{ items: CampaignSummary[] }>('/api/campaigns').then(result => setItems(result.items)).catch(err => setError((err as Error).message))
  }, [])
  useEffect(load, [load])

  return <div className="grid gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">Campañas</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Emails de ofertas y promociones para los clientes a recuperar. Elige el grupo en «A recuperar» con los filtros que necesites y crea la campaña desde allí.</p>
      </div>
      <Button type="button" onClick={onRetention}><Mail size={16} aria-hidden="true" /> Nueva campaña</Button>
    </div>
    <DemoNotice />
    {error && <Alert variant="destructive" className="flex items-center justify-between gap-4"><span>{error}</span><Button type="button" variant="outline" size="sm" onClick={load}>Reintentar</Button></Alert>}
    {!items && !error && <div className="grid gap-2" role="status" aria-label="Cargando campañas">{Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-20 w-full rounded-md" />)}</div>}
    {items && items.length === 0 && <Card className="grid justify-items-center gap-3 px-5 py-12 text-center">
      <h2 className="text-lg font-semibold">Aún no hay campañas</h2>
      <p className="max-w-lg text-sm leading-6 text-muted-foreground">Ve a «A recuperar», filtra los huéspedes a los que quieres escribir y pulsa «Crear campaña». La audiencia, los textos por idioma y la vista previa de cada email se preparan aquí.</p>
      <Button type="button" variant="outline" onClick={onRetention}>Ir a Clientes a recuperar</Button>
    </Card>}
    {items && items.length > 0 && <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <Table className="min-w-[52rem]">
          <TableHeader><TableRow>
            <TableHead>Campaña</TableHead><TableHead>Audiencia</TableHead><TableHead className="text-right">Destinatarios</TableHead><TableHead>Oferta</TableHead><TableHead>Estado</TableHead><TableHead>Actualizada</TableHead>
          </TableRow></TableHeader>
          <TableBody>{items.map(item => <TableRow key={item.id}>
            <TableCell><Button type="button" variant="link" className="h-auto min-h-0 p-0 text-left font-semibold whitespace-normal" onClick={() => onOpen(item.id)}>{item.name}</Button><small className="block text-xs text-muted-foreground">{item.created_by ? `Creada por ${item.created_by}` : 'Creada'} el {formatDate(item.created_at, true)}</small></TableCell>
            <TableCell className="max-w-80 text-sm text-muted-foreground">{filtersText(item.filters)}</TableCell>
            <TableCell className="text-right tabular-nums"><strong className="font-semibold">{integer.format(item.included)}</strong><small className="block text-xs text-muted-foreground">de {integer.format(item.candidates)} candidatos</small></TableCell>
            <TableCell className="font-mono text-xs">{item.offer?.code || '—'}</TableCell>
            <TableCell><Badge className="bg-muted text-muted-foreground">Borrador</Badge></TableCell>
            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">{formatDate(item.updated_at, true)}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </div>
    </Card>}
  </div>
}

type PreviewTarget = { kind: 'sample'; language: CampaignLanguage } | { kind: 'recipient'; id: number; name: string }

function CampaignEditor({ id, onBack }: { id: number; onBack: () => void }) {
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null)
  const [name, setName] = useState('')
  const [content, setContent] = useState<CampaignContent>({})
  const [offer, setOffer] = useState<CampaignOffer>({ code: '', valid_until: '', cta_url: '' })
  const [language, setLanguage] = useState<CampaignLanguage>('es')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [target, setTarget] = useState<PreviewTarget>({ kind: 'sample', language: 'es' })
  const [preview, setPreview] = useState<CampaignPreview | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [device, setDevice] = useState<'desktop' | 'mobile'>('desktop')
  const [plainText, setPlainText] = useState(false)
  const [recipientsVersion, setRecipientsVersion] = useState(0)
  const focused = useRef<{ field: keyof CampaignContentFields; element: HTMLInputElement | HTMLTextAreaElement } | null>(null)
  const previewRef = useRef<HTMLDivElement>(null)

  const apply = useCallback((detail: CampaignDetail) => {
    setCampaign(detail); setName(detail.name); setContent(detail.content); setOffer(detail.offer); setDirty(false)
  }, [])

  useEffect(() => {
    api<CampaignDetail>(`/api/campaigns/${id}`).then(detail => {
      apply(detail)
      const first = detail.audience.languages[0]?.sent_as as CampaignLanguage | undefined
      if (first && detail.content[first]) { setLanguage(first); setTarget({ kind: 'sample', language: first }) }
    }).catch(err => setError((err as Error).message))
  }, [id, apply])

  // The preview follows the editor, saved or not.
  useEffect(() => {
    if (!campaign) return
    const timer = window.setTimeout(() => {
      const body = target.kind === 'recipient' ? { content, offer, recipient_id: target.id } : { content, offer, language: target.language }
      api<CampaignPreview>(`/api/campaigns/${id}/preview`, { method: 'POST', body: JSON.stringify(body) })
        .then(result => { setPreview(result); setPreviewError('') })
        .catch(err => setPreviewError((err as Error).message))
    }, 350)
    return () => window.clearTimeout(timer)
  }, [campaign, id, content, offer, target])

  function updateField(field: keyof CampaignContentFields, value: string) {
    setContent(current => ({ ...current, [language]: { ...(current[language] || campaign!.defaults[language]), [field]: value } }))
    setDirty(true); setNotice('')
  }

  function insertVariable(variable: string) {
    const token = `{${variable}}`
    const target = focused.current
    const fields = content[language] || campaign!.defaults[language]
    if (!target) { updateField('body', `${fields.body}${fields.body.endsWith(' ') || !fields.body ? '' : ' '}${token}`); return }
    const value = fields[target.field]
    const start = target.element.selectionStart ?? value.length
    const end = target.element.selectionEnd ?? value.length
    updateField(target.field, value.slice(0, start) + token + value.slice(end))
    window.requestAnimationFrame(() => { target.element.focus(); target.element.setSelectionRange(start + token.length, start + token.length) })
  }

  function chooseLanguage(code: CampaignLanguage) {
    setLanguage(code)
    if (target.kind === 'sample') setTarget({ kind: 'sample', language: code })
  }

  function toggleLanguage(code: CampaignLanguage) {
    if (code === campaign!.fallback_language) return
    setContent(current => {
      const next = { ...current }
      if (next[code]) delete next[code]
      else next[code] = { ...campaign!.defaults[code] }
      return next
    })
    setDirty(true)
  }

  async function save() {
    setSaving(true); setError(''); setNotice('')
    try {
      apply(await api<CampaignDetail>(`/api/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim() || campaign!.name, content, offer }) }))
      setNotice('Cambios guardados.')
    } catch (err) { setError((err as Error).message) } finally { setSaving(false) }
  }

  async function rebuild() {
    setRebuilding(true); setError(''); setNotice('')
    try {
      const detail = await api<CampaignDetail>(`/api/campaigns/${id}/audience`, { method: 'POST' })
      setCampaign(current => current ? { ...current, audience: detail.audience, audience_built_at: detail.audience_built_at } : detail)
      setRecipientsVersion(value => value + 1)
      setNotice('Audiencia recalculada con los datos actuales.')
    } catch (err) { setError((err as Error).message) } finally { setRebuilding(false) }
  }

  async function remove() {
    if (!window.confirm(`¿Eliminar la campaña «${campaign!.name}»? Se borran sus textos y su audiencia.`)) return
    try { await api(`/api/campaigns/${id}`, { method: 'DELETE' }); onBack() } catch (err) { setError((err as Error).message) }
  }

  function showRecipient(recipient: CampaignRecipient) {
    setTarget({ kind: 'recipient', id: recipient.id, name: recipient.display_name })
    // Edit the text this guest will get: their language, or English when it has no text.
    const own = recipient.language as CampaignLanguage
    setLanguage(recipient.language_known && content[own] ? own : campaign!.fallback_language)
    previewRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
  }

  if (error && !campaign) return <div className="grid gap-4"><Button type="button" variant="ghost" className="justify-self-start" onClick={onBack}><ArrowLeft size={16} aria-hidden="true" /> Campañas</Button><Alert variant="destructive">{error}</Alert></div>
  if (!campaign) return <div className="grid gap-3" role="status" aria-label="Cargando campaña"><Skeleton className="h-16 w-full" /><Skeleton className="h-28 w-full" /><Skeleton className="h-96 w-full" /></div>

  const audience = campaign.audience
  const fields = content[language] || campaign.defaults[language]
  const enabled = !!content[language]
  const countByLanguage = new Map(audience.languages.map(item => [item.language, item.count]))
  // Guests with an unknown language, or one without saved text, get the English version.
  const fallbackCount = audience.fallback

  return <div className="grid gap-5">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="grid min-w-0 flex-1 gap-2">
        <Button type="button" variant="ghost" size="sm" className="justify-self-start px-2" onClick={onBack}><ArrowLeft size={16} aria-hidden="true" /> Campañas</Button>
        <label className="grid gap-1 text-xs font-medium text-muted-foreground">Nombre de la campaña
          <Input className="max-w-xl text-lg font-semibold text-foreground" value={name} maxLength={150} onChange={event => { setName(event.target.value); setDirty(true) }} />
        </label>
        <p className="text-xs text-muted-foreground">{campaign.created_by ? `Creada por ${campaign.created_by}` : 'Creada'} el {formatDate(campaign.created_at, true)} · <Badge className="bg-muted text-muted-foreground">Borrador</Badge></p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="ghost" onClick={() => void remove()}><Trash2 size={16} aria-hidden="true" /> Eliminar</Button>
        <Button type="button" onClick={() => void save()} disabled={!dirty || saving}><Save size={16} aria-hidden="true" /> {saving ? 'Guardando…' : dirty ? 'Guardar cambios' : 'Guardado'}</Button>
      </div>
    </div>

    <DemoNotice />
    {error && <Alert variant="destructive">{error}</Alert>}
    {notice && <Alert variant="success" role="status">{notice}</Alert>}

    <section aria-labelledby="audience-title" className="grid gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="audience-title" className="text-lg font-semibold">1 · Audiencia</h2>
          <p className="text-sm text-muted-foreground">{filtersText(campaign.filters)}. Calculada el {formatDate(campaign.audience_built_at, true)}.</p>
        </div>
        <Button type="button" variant="outline" onClick={() => void rebuild()} disabled={rebuilding}><RefreshCw size={16} aria-hidden="true" className={rebuilding ? 'animate-spin motion-reduce:animate-none' : ''} /> {rebuilding ? 'Recalculando…' : 'Recalcular audiencia'}</Button>
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="Candidatos" value={audience.candidates} detail="Huéspedes que cumplen los filtros" />
        <Metric label="Recibirán el email" value={audience.included} detail="Ficha única, email propio y aceptan campañas" tone="text-success-ink" />
        <Metric label="Excluidos" value={audience.excluded} detail="Por ley, por datos o a mano" />
        <Metric label="Idiomas" value={audience.languages.length} detail={fallbackCount ? `${integer.format(fallbackCount)} reciben la versión en inglés` : 'Todos con texto en su idioma'} />
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Card className="p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Por qué se excluye a los demás</h3>
          {audience.reasons.length ? <ul className="grid gap-1.5 text-sm">{audience.reasons.map(item => <li key={item.reason} className="flex items-center justify-between gap-3"><span>{item.label}</span><strong className="font-semibold tabular-nums">{integer.format(item.count)}</strong></li>)}</ul> : <p className="text-sm text-muted-foreground">Nadie queda excluido.</p>}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">Solo se escribe a quien tiene una única ficha de cliente, un email propio (no el de Booking o Airbnb) y acepta recibir campañas. Las bajas se respetan siempre.</p>
        </Card>
        <Card className="p-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Idioma de los destinatarios</h3>
          {audience.languages.length ? <ul className="grid gap-1.5 text-sm">{audience.languages.map(item => <li key={item.language} className="flex items-center justify-between gap-3"><span>{item.label}{(item.sent_as !== item.language || !content[item.language as CampaignLanguage]) && <small className="ml-2 text-xs text-muted-foreground">recibe la versión en inglés</small>}</span><strong className="font-semibold tabular-nums">{integer.format(item.count)}</strong></li>)}</ul> : <p className="text-sm text-muted-foreground">No hay destinatarios.</p>}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">El idioma sale de la ficha del cliente en Avantio. Si no consta o no hay texto en ese idioma, recibe la versión en inglés.</p>
        </Card>
      </div>
    </section>

    <section aria-labelledby="message-title" className="grid gap-3">
      <h2 id="message-title" className="text-lg font-semibold">2 · Mensaje y oferta</h2>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)] xl:items-start">
        <div className="grid gap-4">
          <Card className="grid gap-3 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-primary">Oferta (común a todos los idiomas)</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1 text-xs font-medium">Código promocional<Input className="font-mono uppercase" value={offer.code} maxLength={40} onChange={event => { setOffer({ ...offer, code: event.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, '') }); setDirty(true) }} /></label>
              <label className="grid gap-1 text-xs font-medium">Reservas hasta el<Input type="date" value={offer.valid_until} onChange={event => { setOffer({ ...offer, valid_until: event.target.value }); setDirty(true) }} /></label>
            </div>
            <label className="grid gap-1 text-xs font-medium">Enlace del botón<Input type="url" placeholder="https://" value={offer.cta_url} onChange={event => { setOffer({ ...offer, cta_url: event.target.value }); setDirty(true) }} /></label>
          </Card>

          <Card className="grid gap-4 p-4">
            <div className="grid gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-primary">Texto por idioma</h3>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Idioma que editas">{campaign.languages.map(item => {
                const count = countByLanguage.get(item.code) || 0
                return <Button key={item.code} type="button" size="sm" variant={language === item.code ? 'secondary' : 'outline'} aria-pressed={language === item.code} className={`h-auto flex-col gap-0 py-1.5 ${content[item.code] ? '' : 'opacity-70'}`} onClick={() => chooseLanguage(item.code)}>
                  <span>{item.label}</span><small className="text-[0.68rem] font-normal text-muted-foreground">{content[item.code] ? `${integer.format(count)} destinatarios` : 'sin texto'}</small>
                </Button>
              })}</div>
            </div>
            {!enabled && <Alert variant="warning">Esta campaña no tiene texto en {campaign.languages.find(item => item.code === language)?.label.toLowerCase()}: quien hable este idioma recibe la versión en inglés. <Button type="button" variant="link" className="h-auto min-h-0 p-0 align-baseline" onClick={() => toggleLanguage(language)}>Añadir este idioma</Button></Alert>}
            {enabled && <>
              <div className="grid gap-1.5">
                <span className="text-xs font-medium">Variables: pulsa para insertarlas donde está el cursor</span>
                <div className="flex flex-wrap gap-1.5">{campaign.variables.map(variable => <Button key={variable.name} type="button" size="sm" variant="outline" className="min-h-8 px-2 font-mono text-xs" title={variable.label} onClick={() => insertVariable(variable.name)}>{`{${variable.name}}`}</Button>)}</div>
              </div>
              {FIELDS.map(field => <label key={field.key} className="grid gap-1 text-xs font-medium">{field.label}
                {field.multiline
                  ? <Textarea rows={11} value={fields[field.key]} onFocus={event => { focused.current = { field: field.key, element: event.currentTarget } }} onChange={event => updateField(field.key, event.target.value)} />
                  : <Input value={fields[field.key]} onFocus={event => { focused.current = { field: field.key, element: event.currentTarget } }} onChange={event => updateField(field.key, event.target.value)} />}
                {field.hint && <span className="font-normal text-muted-foreground">{field.hint}</span>}
              </label>)}
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={() => { setContent(current => ({ ...current, [language]: { ...campaign.defaults[language] } })); setDirty(true) }}><RotateCcw size={15} aria-hidden="true" /> Restaurar texto propuesto</Button>
                {language !== campaign.fallback_language && <Button type="button" variant="ghost" size="sm" onClick={() => toggleLanguage(language)}>Quitar este idioma</Button>}
              </div>
            </>}
          </Card>
        </div>

        <div ref={previewRef} className="grid scroll-mt-4 gap-3 xl:sticky xl:top-4">
          <Card className="grid gap-3 p-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <label className="grid min-w-56 flex-1 gap-1 text-xs font-medium">Ver el email de
                <Select value={target.kind === 'recipient' ? `r${target.id}` : `s${target.language}`} onChange={event => {
                  const value = event.target.value
                  if (value.startsWith('s')) setTarget({ kind: 'sample', language: value.slice(1) as CampaignLanguage })
                }}>
                  {target.kind === 'recipient' && <option value={`r${target.id}`}>{target.name}</option>}
                  {campaign.languages.filter(item => content[item.code]).map(item => <option key={item.code} value={`s${item.code}`}>Cliente de ejemplo · {item.label}</option>)}
                </Select>
              </label>
              <div className="flex rounded-md border border-border bg-background p-1" role="group" aria-label="Tamaño de la vista previa">
                <Button type="button" size="sm" variant={device === 'desktop' && !plainText ? 'secondary' : 'ghost'} aria-pressed={device === 'desktop' && !plainText} onClick={() => { setDevice('desktop'); setPlainText(false) }}><Monitor size={15} aria-hidden="true" /> Escritorio</Button>
                <Button type="button" size="sm" variant={device === 'mobile' && !plainText ? 'secondary' : 'ghost'} aria-pressed={device === 'mobile' && !plainText} onClick={() => { setDevice('mobile'); setPlainText(false) }}><Smartphone size={15} aria-hidden="true" /> Móvil</Button>
                <Button type="button" size="sm" variant={plainText ? 'secondary' : 'ghost'} aria-pressed={plainText} onClick={() => setPlainText(true)}>Texto</Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">Elige un destinatario en la lista de abajo con «Ver email» para comprobar su versión personalizada.</p>
          </Card>
          {previewError && <Alert variant="destructive">{previewError}</Alert>}
          {preview && <Card className="overflow-hidden p-0">
            <dl className="grid gap-1 border-b border-border bg-muted/40 px-4 py-3 text-sm">
              <div className="flex gap-2"><dt className="w-14 shrink-0 text-muted-foreground">De</dt><dd className="min-w-0 truncate">{preview.from}</dd></div>
              <div className="flex gap-2"><dt className="w-14 shrink-0 text-muted-foreground">Para</dt><dd className="min-w-0 truncate">{preview.to || 'Cliente de ejemplo'}</dd></div>
              <div className="flex gap-2"><dt className="w-14 shrink-0 text-muted-foreground">Asunto</dt><dd className="min-w-0 font-semibold">{preview.subject || '—'}</dd></div>
              <div className="flex gap-2"><dt className="w-14 shrink-0 text-muted-foreground">Vista</dt><dd className="min-w-0 text-muted-foreground">{preview.preheader || '—'}</dd></div>
            </dl>
            {preview.warnings.length > 0 && <div className="border-b border-border px-4 py-3"><Alert variant="warning" role="status"><ul className="grid gap-1">{preview.warnings.map(item => <li key={item}>{item}</li>)}</ul></Alert></div>}
            {plainText
              ? <pre className="max-h-[75vh] overflow-auto whitespace-pre-wrap bg-white p-5 text-sm leading-6 text-neutral-900">{preview.text}</pre>
              : <div className="bg-neutral-200/60 p-3"><iframe className={`mx-auto block h-[75vh] min-h-[32rem] border-0 bg-white shadow-sm transition-[width] motion-reduce:transition-none ${device === 'mobile' ? 'w-[375px] max-w-full' : 'w-full'}`} title={`Vista previa del email: ${preview.subject}`} sandbox="" referrerPolicy="no-referrer" srcDoc={preview.html} /></div>}
          </Card>}
        </div>
      </div>
    </section>

    <Recipients campaignId={id} version={recipientsVersion} onShow={showRecipient} onChanged={audience => setCampaign(current => current ? { ...current, audience } : current)} />

    <section aria-labelledby="send-title" className="grid gap-3">
      <h2 id="send-title" className="text-lg font-semibold">4 · Envío</h2>
      <Card className="grid gap-3 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
        <div className="text-sm leading-6">
          <p className="font-semibold">{integer.format(audience.included)} {audience.included === 1 ? 'cliente recibiría' : 'clientes recibirían'} este email.</p>
          <p className="text-muted-foreground">El envío está desactivado hasta conectar el proveedor de correo y su dominio de envío. Entonces saldrá por lotes, con baja en un clic en cada email y seguimiento de clics y reservas.</p>
        </div>
        <Button type="button" disabled title="Envío desactivado hasta conectar el proveedor de email"><Send size={16} aria-hidden="true" /> Enviar campaña</Button>
      </Card>
    </section>
  </div>
}

function Recipients({ campaignId, version, onShow, onChanged }: { campaignId: number; version: number; onShow: (recipient: CampaignRecipient) => void; onChanged: (audience: CampaignDetail['audience']) => void }) {
  const [status, setStatus] = useState<'included' | 'excluded'>('included')
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<CampaignRecipient[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  const requestVersion = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const load = useCallback(async (offset: number) => {
    const current = ++requestVersion.current
    setLoading(true); setError('')
    const params = new URLSearchParams({ status, offset: String(offset), limit: String(RECIPIENTS_PAGE) })
    if (query) params.set('q', query)
    try {
      const result = await api<{ items: CampaignRecipient[]; total: number }>(`/api/campaigns/${campaignId}/recipients?${params}`)
      if (current !== requestVersion.current) return
      setItems(previous => offset ? [...previous, ...result.items] : result.items); setTotal(result.total)
    } catch (err) { if (current === requestVersion.current) setError((err as Error).message) }
    finally { if (current === requestVersion.current) setLoading(false) }
  }, [campaignId, status, query])

  useEffect(() => { void load(0) }, [load, version])

  async function toggle(recipient: CampaignRecipient) {
    setBusy(recipient.id); setError('')
    try {
      const result = await api<{ recipient: CampaignRecipient; audience: CampaignDetail['audience'] }>(`/api/campaigns/${campaignId}/recipients/${recipient.id}`, { method: 'PATCH', body: JSON.stringify({ excluded: recipient.status === 'included' }) })
      setItems(current => current.filter(item => item.id !== recipient.id)); setTotal(value => value - 1)
      onChanged(result.audience)
    } catch (err) { setError((err as Error).message) } finally { setBusy(null) }
  }

  return <section aria-labelledby="recipients-title" className="grid gap-3">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 id="recipients-title" className="text-lg font-semibold">3 · Destinatarios</h2>
        <p className="text-sm text-muted-foreground">Revisa a quién se escribe. Puedes excluir a cualquiera a mano o abrir su email personalizado.</p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex rounded-md border border-border bg-background p-1" role="group" aria-label="Destinatarios a mostrar">
          <Button type="button" size="sm" variant={status === 'included' ? 'secondary' : 'ghost'} aria-pressed={status === 'included'} onClick={() => setStatus('included')}>Incluidos</Button>
          <Button type="button" size="sm" variant={status === 'excluded' ? 'secondary' : 'ghost'} aria-pressed={status === 'excluded'} onClick={() => setStatus('excluded')}>Excluidos</Button>
        </div>
        <label className="relative grid gap-1 text-xs font-medium"><span className="sr-only">Buscar destinatario</span>
          <span className="relative"><Search size={16} aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" /><Input className="w-64 pl-9" type="search" placeholder="Nombre o email" value={search} onChange={event => setSearch(event.target.value)} /></span>
        </label>
      </div>
    </div>
    {error && <Alert variant="destructive">{error}</Alert>}
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <Table className="min-w-[60rem]">
          <TableHeader><TableRow>
            <TableHead>Huésped</TableHead><TableHead>Idioma</TableHead><TableHead>Historial</TableHead><TableHead>Última estancia</TableHead>{status === 'excluded' && <TableHead>Motivo</TableHead>}<TableHead className="text-right">Acciones</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {items.map(item => <TableRow key={item.id}>
              <TableCell className="max-w-72"><strong className="block truncate font-semibold" title={item.display_name}>{item.display_name}</strong><small className="block truncate text-xs text-muted-foreground">{item.email || 'Sin email'}</small></TableCell>
              <TableCell className="text-sm">{item.language_raw || '—'}{!item.language_known && <small className="block text-xs text-muted-foreground">recibe inglés</small>}</TableCell>
              <TableCell className="whitespace-nowrap text-sm tabular-nums">{item.stays} {item.stays === 1 ? 'estancia' : 'estancias'}<small className="block text-xs text-muted-foreground">{item.years.join(', ')}</small></TableCell>
              <TableCell className="max-w-64 text-sm">{formatDate(item.last_stay_start)}<small className="block truncate text-xs text-muted-foreground" title={item.property_name || undefined}>{item.property_name || '—'}</small></TableCell>
              {status === 'excluded' && <TableCell className="text-sm">{item.exclusion_label}</TableCell>}
              <TableCell className="text-right"><div className="flex justify-end gap-1">
                {(item.status === 'included' || item.exclusion_reason === 'manual') && <Button type="button" variant="ghost" size="sm" onClick={() => onShow(item)}><Eye size={15} aria-hidden="true" /> Ver email</Button>}
                {item.status === 'included' && <Button type="button" variant="ghost" size="sm" disabled={busy === item.id} onClick={() => void toggle(item)}><UserMinus size={15} aria-hidden="true" /> Excluir</Button>}
                {item.exclusion_reason === 'manual' && <Button type="button" variant="ghost" size="sm" disabled={busy === item.id} onClick={() => void toggle(item)}><UserPlus size={15} aria-hidden="true" /> Incluir</Button>}
              </div></TableCell>
            </TableRow>)}
            {!loading && items.length === 0 && <TableRow><TableCell colSpan={6} className="py-8 text-center text-sm text-muted-foreground">{query ? 'Nadie coincide con la búsqueda.' : status === 'included' ? 'Nadie recibiría esta campaña con los filtros elegidos.' : 'Nadie queda excluido.'}</TableCell></TableRow>}
          </TableBody>
        </Table>
      </div>
    </Card>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground" aria-live="polite">{loading && !items.length ? 'Cargando…' : `${integer.format(items.length)} de ${integer.format(total)}`}</p>
      {items.length < total && <Button type="button" variant="outline" disabled={loading} onClick={() => void load(items.length)}>{loading ? 'Cargando…' : 'Mostrar más'}</Button>}
    </div>
  </section>
}
