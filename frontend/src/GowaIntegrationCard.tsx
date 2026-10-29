import { useCallback, useEffect, useState } from 'react'
import { CircleCheck, LoaderCircle, QrCode, RefreshCw, WifiOff } from 'lucide-react'
import { api } from './api'
import type { GowaPairing, GowaStatus } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Skeleton } from './components/ui/skeleton'

type PairingQr = { image: string; expiresAt: number }

function statusText(status: GowaStatus | null, error: string) {
  if (error && !status) return 'No disponible'
  if (!status) return 'Comprobando…'
  if (!status.configured) return 'Pendiente de configurar'
  if (status.is_connected && status.is_logged_in) return 'Conectado'
  if (status.is_logged_in) return 'Desconectado'
  return 'Sin vincular'
}

function statusClass(status: GowaStatus | null, error: string) {
  if (error && !status) return 'bg-error-bg text-error-ink'
  if (!status || !status.configured) return 'bg-warning-bg text-warning-ink'
  if (status.is_connected && status.is_logged_in) return 'bg-success-bg text-success-ink'
  if (status.is_logged_in) return 'bg-warning-bg text-warning-ink'
  return 'bg-muted text-muted-foreground'
}

export function GowaIntegrationCard() {
  const [status, setStatus] = useState<GowaStatus | null>(null)
  const [qr, setQr] = useState<PairingQr | null>(null)
  const [secondsLeft, setSecondsLeft] = useState(0)
  const [pairingPending, setPairingPending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refreshStatus = useCallback(async () => {
    const next = await api<GowaStatus>('/api/integrations/gowa')
    setStatus(next)
    setError('')
    if (next.is_logged_in && next.is_connected) {
      setQr(null)
      setPairingPending(false)
    }
    return next
  }, [])

  const loadStatus = useCallback(async () => {
    setLoading(true)
    try { await refreshStatus() }
    catch (err) { setError((err as Error).message || 'No se pudo consultar GOWA.') }
    finally { setLoading(false) }
  }, [refreshStatus])

  useEffect(() => { void loadStatus() }, [loadStatus])

  useEffect(() => {
    if (!pairingPending) return
    const timer = window.setInterval(() => {
      void refreshStatus().catch(err => {
        setError((err as Error).message || 'No se pudo consultar GOWA.')
        setPairingPending(false)
      })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [pairingPending, refreshStatus])

  useEffect(() => {
    if (!qr) return
    const updateCountdown = () => {
      const remaining = Math.max(0, Math.ceil((qr.expiresAt - Date.now()) / 1000))
      setSecondsLeft(remaining)
      if (remaining === 0) {
        setQr(current => current?.expiresAt === qr.expiresAt && current.image
          ? { ...current, image: '' }
          : current)
      }
    }
    updateCountdown()
    const timer = window.setInterval(updateCountdown, 1000)
    return () => window.clearInterval(timer)
  }, [qr?.expiresAt])

  async function connect() {
    setBusy(true)
    setError('')
    try {
      const pairing = await api<GowaPairing>('/api/integrations/gowa/connect', { method: 'POST' })
      setQr({ image: pairing.qr_image, expiresAt: Date.now() + pairing.qr_duration * 1000 })
      setPairingPending(true)
      await refreshStatus()
    } catch (err) {
      setError((err as Error).message || 'No se pudo iniciar el emparejamiento.')
      setPairingPending(false)
    } finally { setBusy(false) }
  }

  async function reconnect() {
    setBusy(true)
    setError('')
    try {
      await api('/api/integrations/gowa/reconnect', { method: 'POST' })
      setPairingPending(true)
      await refreshStatus()
    } catch (err) {
      setError((err as Error).message || 'No se pudo solicitar la reconexión.')
      setPairingPending(false)
    } finally { setBusy(false) }
  }

  const connected = !!status?.is_connected && !!status?.is_logged_in
  const canManage = !!status?.configured

  return <Card className="p-5 sm:p-6" aria-labelledby="gowa-title">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CANAL DE MENSAJERÍA</p>
        <h2 id="gowa-title" className="text-xl font-semibold">WhatsApp</h2>
      </div>
      <Badge className={statusClass(status, error)} aria-live="polite">{statusText(status, error)}</Badge>
    </div>

    <p className="my-4 text-sm leading-6 text-muted-foreground">
      Vincula el teléfono de atención para recibir consultas de WhatsApp en la bandeja de AvantIA.
    </p>

    {loading && !status && <div className="grid gap-2" role="status" aria-label="Comprobando estado">
      <Skeleton className="h-4 w-36" /><Skeleton className="h-4 w-56" />
    </div>}

    {status && !status.configured && <Alert variant="warning" className="mb-4">
      Faltan las credenciales de GOWA o el secreto de webhook en la configuración del servidor.
    </Alert>}

    {error && <Alert variant="destructive" className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <span>{error}</span>
      {!status && <Button variant="outline" size="sm" onClick={() => void loadStatus()} disabled={loading}>
        Volver a intentar
      </Button>}
    </Alert>}

    {connected && <Alert variant="success" className="mb-4 flex items-center gap-2">
      <CircleCheck size={17} aria-hidden="true" /> WhatsApp está conectado y listo para recibir mensajes.
    </Alert>}

    {status?.is_logged_in && !connected && <Alert variant="warning" className="mb-4 flex items-center gap-2">
      <WifiOff size={17} aria-hidden="true" /> La sesión sigue vinculada, pero GOWA está desconectado.
    </Alert>}

    {qr?.image && !connected && <div className="mb-4 grid justify-items-center gap-3 rounded-lg border border-border bg-background p-4 text-center">
      <img src={qr.image} alt="Código QR para vincular WhatsApp Web" className="size-56 max-w-full rounded bg-white p-2" />
      <div>
        <p className="text-sm font-medium">Escanea el código desde WhatsApp</p>
        <p className="mt-1 text-xs text-muted-foreground">Dispositivos vinculados → Vincular dispositivo</p>
        <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">El QR caduca en {secondsLeft} s</p>
      </div>
    </div>}

    {qr && !qr.image && !connected && <Alert variant="warning" className="mb-4">
      El código QR ha caducado. Genera uno nuevo para continuar.
    </Alert>}

    <div className="flex flex-wrap gap-2">
      {canManage && !status?.is_logged_in && <Button onClick={() => void connect()} disabled={busy || loading}>
        {busy ? <LoaderCircle size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <QrCode size={16} aria-hidden="true" />}
        {qr ? 'Generar otro QR' : 'Conectar con QR'}
      </Button>}
      {canManage && status?.is_logged_in && !connected && <Button onClick={() => void reconnect()} disabled={busy || loading}>
        {busy ? <LoaderCircle size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}
        Reconectar
      </Button>}
      <Button variant="outline" onClick={() => void loadStatus()} disabled={busy || loading}>
        <RefreshCw size={15} aria-hidden="true" /> Actualizar estado
      </Button>
    </div>

    <p className="mt-4 text-xs leading-5 text-muted-foreground">El modo de solo lectura sigue bloqueando los envíos desde AvantIA.</p>
  </Card>
}
