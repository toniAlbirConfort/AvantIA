import { useRef, useState } from 'react'
import { PauseCircle, PlayCircle } from 'lucide-react'
import { api } from './api'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'

export type AutoReplyStatus = {
  mode: 'off' | 'shadow' | 'send'
  configured_mode: 'off' | 'shadow' | 'send'
  paused: boolean
  paused_at: string | null
  paused_by_id: number | null
  timezone: string
  night_start: string
  night_end: string
}

type Props = {
  value: AutoReplyStatus
  isAdmin: boolean
  userName: (id: number | null) => string
  onChanged: () => void
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—'
}

function state(value: AutoReplyStatus) {
  if (value.paused) return { label: 'Pausada', className: 'bg-error-bg text-error-ink' }
  if (value.mode === 'send') return { label: 'Activa', className: 'bg-success-bg text-success-ink' }
  if (value.mode === 'shadow') return { label: 'Modo sombra', className: 'bg-info-bg text-info-ink' }
  return { label: 'Desactivada', className: 'bg-muted text-muted-foreground' }
}

export function AutoReplyCard({ value, isAdmin, userName, onChanged }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const hours = `de ${value.night_start} a ${value.night_end} y fines de semana (${value.timezone})`
  const pausing = !value.paused
  const badge = state(value)
  // Nothing to pause when .env already leaves it off; resuming is always possible.
  const canToggle = isAdmin && (value.paused || value.configured_mode !== 'off')

  async function confirm() {
    setBusy(true); setError('')
    try {
      await api('/api/auto-reply/pause', { method: 'POST', body: JSON.stringify({ paused: pausing }) })
      dialogRef.current?.close()
      onChanged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <Card className="p-5">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CANAL / FUENTE</p>
    <h2 className="mb-3 text-xl font-semibold">Autorespuesta fuera de horario</h2>
    <Badge className={badge.className}>{badge.label}</Badge>
    <p className="my-4 text-sm leading-6 text-muted-foreground">
      {value.paused
        ? 'Pausada desde la app: no se envía ninguna autorespuesta hasta que se reactive.'
        : value.mode === 'send'
          ? `Envía un acuse a las consultas nuevas de clientes recibidas ${hours}.`
          : value.mode === 'shadow'
            ? `Registra, sin enviar, el acuse que recibirían las consultas nuevas ${hours}.`
            : 'Desactivada. Se activa con AUTO_REPLY_MODE en el .env del servidor.'}
    </p>
    {value.paused && <small className="mb-3 block text-xs text-muted-foreground">Pausada el {dateTime(value.paused_at)} por {userName(value.paused_by_id)}</small>}
    {canToggle && <Button
      type="button"
      variant={pausing ? 'destructive' : 'default'}
      onClick={() => { setError(''); dialogRef.current?.showModal() }}
    >
      {pausing ? <><PauseCircle size={16} aria-hidden="true" /> Desactivar ahora</> : <><PlayCircle size={16} aria-hidden="true" /> Reactivar</>}
    </Button>}
    <dialog
      ref={dialogRef}
      aria-labelledby="auto-reply-confirm-title"
      className="m-auto w-[92vw] max-w-md rounded-xl border border-border bg-card p-0 text-foreground shadow-2xl backdrop:bg-background/80"
    >
      <div className="grid gap-4 p-5 sm:p-6">
        <h3 id="auto-reply-confirm-title" className="text-lg font-semibold">
          {pausing ? '¿Desactivar la autorespuesta ahora?' : '¿Reactivar la autorespuesta?'}
        </h3>
        <p className="text-sm leading-6 text-muted-foreground">
          {pausing
            ? 'Deja de enviarse al instante, también para los correos que ya estén en cola. Los clientes que escriban fuera de horario no recibirán ningún acuse hasta que alguien la reactive.'
            : value.configured_mode === 'send'
              ? `Se volverá a enviar un acuse a las consultas nuevas de clientes recibidas ${hours}.`
              : 'Vuelve al modo configurado en el servidor.'}
        </p>
        {error && <Alert variant="destructive">{error}</Alert>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={() => dialogRef.current?.close()}>Cancelar</Button>
          <Button type="button" variant={pausing ? 'destructive' : 'default'} disabled={busy} onClick={() => void confirm()}>
            {busy ? 'Guardando…' : pausing ? 'Sí, desactivar' : 'Sí, reactivar'}
          </Button>
        </div>
      </div>
    </dialog>
  </Card>
}
