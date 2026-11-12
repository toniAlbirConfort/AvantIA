import { useEffect, useState } from 'react'
import { api } from './api'
import type { AnnotationExpected, AnnotationIntent, AnnotationSummary, ContactInfo, Message } from './types'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'

const groups: Array<[AnnotationIntent['label'], string]> = [
  ['demand', 'Demanda'],
  ['guest_service', 'Atención de reserva'],
  ['non_demand', 'No demanda'],
]

let intentsRequest: Promise<AnnotationIntent[]> | null = null
function loadIntents() {
  intentsRequest ??= api<AnnotationIntent[]>('/api/annotations/intents').catch(error => {
    intentsRequest = null
    throw error
  })
  return intentsRequest
}

function useIntents() {
  const [intents, setIntents] = useState<AnnotationIntent[]>([])
  useEffect(() => {
    let active = true
    loadIntents().then(items => { if (active) setIntents(items) }).catch(() => undefined)
    return () => { active = false }
  }, [])
  return intents
}

function initialExpected(message: Message, contact: ContactInfo | null): AnnotationExpected {
  const saved = message.annotation?.expected
  const source = saved || message.classification?.extracted || {}
  return {
    arrival: source.arrival || null,
    departure: source.departure || null,
    guests: source.guests || null,
    property_query: source.property_query || null,
    contact_email: saved ? saved.contact_email || null : contact?.emails[0] || null,
    contact_phone: saved ? saved.contact_phone || null : contact?.phones[0] || null,
    wants_price: saved ? saved.wants_price ?? null : contact ? contact.wants_price : null,
  }
}

export function MessageAnnotationControl({
  message,
  contact = null,
  disabled,
  onSave,
}: {
  message: Message
  contact?: ContactInfo | null
  disabled: boolean
  onSave: (body: { intent: string; expected: AnnotationExpected | null; note: string | null }) => void
}) {
  const intents = useIntents()
  const annotation = message.annotation
  const [intent, setIntent] = useState(annotation?.intent || '')
  const [expected, setExpected] = useState<AnnotationExpected>(() => initialExpected(message, contact))
  const [note, setNote] = useState(annotation?.note || '')
  const [error, setError] = useState('')

  useEffect(() => {
    setIntent(message.annotation?.intent || '')
    setExpected(initialExpected(message, contact))
    setNote(message.annotation?.note || '')
    setError('')
  }, [message.id, message.annotation?.updated_at])

  const selected = intents.find(item => item.intent === intent)
  const fromModel = !annotation?.expected && (!!message.classification?.extracted || !!contact)
  const set = (field: keyof AnnotationExpected, value: string) => {
    setError('')
    setExpected(current => ({
      ...current,
      [field]: field === 'guests' ? (value ? Number(value) : null) : (value || null),
    }))
  }
  const save = () => {
    if (!intent) { setError('Elige primero el tipo de mensaje.'); return }
    if (selected?.needs_availability && expected.arrival && expected.departure && expected.arrival >= expected.departure) {
      setError('La salida debe ser posterior a la llegada.'); return
    }
    onSave({ intent, expected: selected?.needs_availability ? expected : null, note: note.trim() || null })
  }

  return <div className="mt-3 grid gap-3 rounded-md border border-dashed border-border bg-background p-3">
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="font-semibold uppercase tracking-wide text-primary">Etiquetado manual</span>
      {annotation
        ? <>
          <Badge className="bg-success-bg text-success-ink">Etiquetado</Badge>
          <span className="text-muted-foreground">{annotation.split === 'eval' ? 'Reservado para medir el modelo' : 'Disponible como ejemplo'}</span>
        </>
        : <span className="text-muted-foreground">Indica qué es este mensaje para enseñar y medir a la IA.</span>}
    </div>
    <label className="grid gap-1 text-xs font-medium">
      Tipo de mensaje
      <Select className="h-9" aria-label="Tipo de mensaje" value={intent} disabled={disabled} onChange={event => { setIntent(event.target.value); setError('') }}>
        <option value="">Selecciona…</option>
        {groups.map(([label, name]) => <optgroup key={label} label={name}>
          {intents.filter(item => item.label === label).map(item => <option key={item.intent} value={item.intent}>{item.name}</option>)}
        </optgroup>)}
      </Select>
    </label>
    {selected?.needs_availability && <div className="grid gap-2">
      <p className="m-0 text-xs leading-5 text-muted-foreground">
        Escribe lo que pide el cliente. Deja en blanco lo que no diga.{fromModel && ' Valores propuestos por la IA: compruébalos.'}
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="grid gap-1 text-xs font-medium">Llegada<Input className="h-9" type="date" value={expected.arrival || ''} disabled={disabled} onChange={event => set('arrival', event.target.value)} /></label>
        <label className="grid gap-1 text-xs font-medium">Salida<Input className="h-9" type="date" value={expected.departure || ''} disabled={disabled} onChange={event => set('departure', event.target.value)} /></label>
        <label className="grid gap-1 text-xs font-medium">Personas<Input className="h-9" type="number" min={1} max={100} step={1} value={expected.guests ?? ''} disabled={disabled} onChange={event => set('guests', event.target.value)} /></label>
        <label className="grid gap-1 text-xs font-medium">Alojamiento<Input className="h-9" maxLength={150} placeholder="Si lo nombra" value={expected.property_query || ''} disabled={disabled} onChange={event => set('property_query', event.target.value)} /></label>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="grid gap-1 text-xs font-medium">Email del cliente<Input className="h-9" type="email" maxLength={255} placeholder="A quién responder" value={expected.contact_email || ''} disabled={disabled} onChange={event => set('contact_email', event.target.value)} /></label>
        <label className="grid gap-1 text-xs font-medium">Teléfono del cliente<Input className="h-9" type="tel" maxLength={40} placeholder="+34…" value={expected.contact_phone || ''} disabled={disabled} onChange={event => set('contact_phone', event.target.value)} /></label>
        <label className="grid gap-1 text-xs font-medium">Qué pide<Select className="h-9" value={expected.wants_price === true ? 'price' : expected.wants_price === false ? 'availability' : ''} disabled={disabled} onChange={event => { setError(''); setExpected(current => ({ ...current, wants_price: event.target.value === 'price' ? true : event.target.value === 'availability' ? false : null })) }}>
          <option value="">Sin indicar</option>
          <option value="price">Precio u oferta</option>
          <option value="availability">Solo disponibilidad</option>
        </Select></label>
      </div>
    </div>}
    <label className="grid gap-1 text-xs font-medium">
      Nota (opcional)
      <Input className="h-9" maxLength={300} value={note} disabled={disabled} onChange={event => setNote(event.target.value)} placeholder="Por qué lo clasificas así" />
    </label>
    {error && <p className="m-0 text-xs text-destructive" role="alert">{error}</p>}
    <Button className="w-fit" size="sm" disabled={disabled} onClick={save}>{annotation ? 'Actualizar etiqueta' : 'Guardar etiqueta'}</Button>
  </div>
}

const percent = (value: number | null) => value === null ? '—' : `${Math.round(value * 100)} %`

export function AnnotationProgress({ version }: { version: number }) {
  const [summary, setSummary] = useState<AnnotationSummary | null>(null)
  useEffect(() => {
    let active = true
    api<AnnotationSummary>('/api/annotations/summary').then(value => { if (active) setSummary(value) }).catch(() => undefined)
    return () => { active = false }
  }, [version])
  if (!summary) return null
  const model = summary.stored_model
  const dates = ['arrival', 'departure', 'guests'].map(field => model.extraction[field]).filter(item => item?.total)
  const datesTotal = dates.reduce((sum, item) => sum + item.total, 0)
  const datesCorrect = dates.reduce((sum, item) => sum + item.correct, 0)
  return <div className="grid grid-cols-3 gap-2 rounded-md bg-muted/60 p-3 text-xs" aria-label="Progreso del etiquetado">
    <div><p className="m-0 text-muted-foreground">Etiquetados</p><p className="m-0 text-lg font-semibold">{summary.total}</p></div>
    <div><p className="m-0 text-muted-foreground">Acierto del tipo</p><p className="m-0 text-lg font-semibold">{percent(model.label_accuracy)}</p></div>
    <div><p className="m-0 text-muted-foreground">Acierto de fechas</p><p className="m-0 text-lg font-semibold">{percent(datesTotal ? datesCorrect / datesTotal : null)}</p></div>
  </div>
}
