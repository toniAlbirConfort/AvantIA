import { useState } from 'react'
import { CircleAlert, CircleCheck, Clock } from 'lucide-react'
import type { RegistrationCheck } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Input } from './components/ui/input'
import { Select } from './components/ui/select'

export const DISMISS_REASONS: Record<string, string> = {
  no_es_consulta: 'No es una consulta de alojamiento',
  huesped_existente: 'Huésped con reserva ya hecha',
  proveedor: 'Proveedor, agencia o publicidad',
  registrada_otro_nombre: 'Registrada en Avantio con otro nombre',
  otro: 'Otro motivo',
}

const AUTOMATIC_REASONS: Record<string, string> = {
  reserva: 'reserva con el mismo apellido creada esos días',
  plantilla_avantio: 'el cliente respondió a la oferta enviada desde Avantio',
  registrada_tras_aviso: 'la reserva apareció después del aviso',
  vinculada_manual: 'vinculada a mano por un operador',
  plataforma_o_interno: 'plataforma o remitente interno',
  interno_sin_cliente: 'correo interno sin datos de cliente',
  no_demanda: 'clasificado como no demanda',
  atencion_huesped: 'atención a un huésped',
  respuesta: 'es una respuesta a otro correo',
  no_consulta: 'no pide alojamiento ni fechas',
  seguimiento: 'seguimiento de una consulta anterior del mismo cliente',
  huesped_con_reserva: 'el cliente ya tiene una reserva en curso',
  parece_huesped: 'el texto es de un huésped con reserva',
}

export function reasonText(reason: string | null) {
  if (!reason) return ''
  if (reason.startsWith('operador:')) return DISMISS_REASONS[reason.slice(9)] || reason
  return AUTOMATIC_REASONS[reason] || reason
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—'
}

type Props = {
  check: RegistrationCheck
  busy: boolean
  userName: (id: number | null) => string
  onAction: (path: string, body: Record<string, unknown>) => void
}

export function RegistrationPanel({ check, busy, userName, onAction }: Props) {
  const [locator, setLocator] = useState('')
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const open = check.status === 'alerted' || check.status === 'pending'
  const shadow = check.mode === 'shadow' || check.mode === null
  const byOperator = check.resolved_by_id !== null

  return <div className="grid gap-3 border-b border-border bg-background px-4 py-3 text-sm sm:px-6" aria-label="Registro en Avantio">
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-semibold uppercase tracking-wide text-primary">Registro en Avantio</span>
      {check.status === 'alerted' && <Badge className="bg-error-bg text-error-ink">Sin registrar</Badge>}
      {check.status === 'pending' && <Badge className="bg-warning-bg text-warning-ink">Pendiente de comprobar</Badge>}
      {check.status === 'registered' && <Badge className="bg-success-bg text-success-ink">Registrada{check.locator ? ` · ${check.locator}` : ''}</Badge>}
      {check.status === 'excluded' && <Badge className="bg-muted text-muted-foreground">No se comprueba</Badge>}
      {check.status === 'alerted' && shadow && <span className="text-xs text-muted-foreground">Modo sombra: no se ha avisado a nadie</span>}
    </div>

    {check.status === 'alerted' && <Alert variant="warning" className="flex gap-2">
      <CircleAlert aria-hidden="true" />
      <span>Recibida el {dateTime(check.received_at)}. Pasada la hora de oficina ({dateTime(check.due_at)}) no hay ninguna reserva de este cliente en Avantio. Regístrala como petición de información aunque luego se descarte.</span>
    </Alert>}
    {check.status === 'pending' && <p className="m-0 flex items-center gap-1.5 text-xs text-muted-foreground"><Clock size={14} aria-hidden="true" />Se comprobará tras la próxima sincronización con Avantio. Avisa si sigue sin reserva después del {dateTime(check.due_at)}.</p>}
    {check.status === 'registered' && <p className="m-0 flex items-center gap-1.5 text-xs text-muted-foreground"><CircleCheck size={14} aria-hidden="true" />{reasonText(check.reason)}{byOperator ? ` · ${userName(check.resolved_by_id)} el ${dateTime(check.resolved_at)}` : ''}</p>}
    {check.status === 'excluded' && <p className="m-0 text-xs text-muted-foreground">Motivo: {reasonText(check.reason)}{check.note ? ` (${check.note})` : ''}{byOperator ? ` · ${userName(check.resolved_by_id)} el ${dateTime(check.resolved_at)}` : ''}</p>}

    {open && <div className="grid gap-3">
      <form className="grid gap-1" onSubmit={event => { event.preventDefault(); onAction(`/api/registration-checks/${check.id}/link`, { locator }) }}>
        <label className="text-xs font-medium" htmlFor={`registration-locator-${check.id}`}>Ya está en Avantio con el localizador</label>
        <div className="flex gap-2">
          <Input id={`registration-locator-${check.id}`} className="h-9 min-w-0 flex-1" placeholder="34777127-130661" value={locator} onChange={event => setLocator(event.target.value)} />
          <Button type="submit" size="sm" className="h-9 shrink-0" disabled={busy || !locator.trim()}>Vincular</Button>
        </div>
      </form>
      <form className="grid gap-1" onSubmit={event => { event.preventDefault(); onAction(`/api/registration-checks/${check.id}/dismiss`, { reason, note: note.trim() || null }) }}>
        <label className="text-xs font-medium" htmlFor={`registration-reason-${check.id}`}>No hay que registrarla porque</label>
        <div className="flex flex-wrap gap-2">
          <span className="min-w-0 flex-1 basis-48"><Select id={`registration-reason-${check.id}`} className="h-9 text-xs" value={reason} onChange={event => setReason(event.target.value)}>
            <option value="">Elige un motivo…</option>
            {Object.entries(DISMISS_REASONS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </Select></span>
          {reason && <Input className="h-9 min-w-0 flex-1 basis-40" aria-label="Nota" placeholder={reason === 'otro' ? 'Explica el motivo' : 'Nota (opcional)'} maxLength={300} value={note} onChange={event => setNote(event.target.value)} />}
          <Button type="submit" size="sm" variant="secondary" className="h-9 shrink-0" disabled={busy || !reason || (reason === 'otro' && !note.trim())}>No procede</Button>
        </div>
      </form>
    </div>}
  </div>
}
