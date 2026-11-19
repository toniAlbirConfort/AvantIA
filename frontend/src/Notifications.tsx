import { useCallback, useEffect, useState } from 'react'
import { CircleAlert, CircleCheck, Info, OctagonAlert, type LucideIcon } from 'lucide-react'
import { api } from './api'
import type { AppNotification, NotificationCounts, NotificationSeverity } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Select } from './components/ui/select'
import { Skeleton } from './components/ui/skeleton'

type Scope = 'open' | 'all'
type NotificationList = { items: AppNotification[]; counts: NotificationCounts }

const severities: Record<NotificationSeverity, { label: string; icon: LucideIcon; tone: string }> = {
  critical: { label: 'Crítico', icon: OctagonAlert, tone: 'bg-error-bg text-error-ink' },
  action: { label: 'Requiere acción', icon: CircleAlert, tone: 'bg-warning-bg text-warning-ink' },
  info: { label: 'Informativo', icon: Info, tone: 'bg-info-bg text-info-ink' },
}
const statusLabels: Record<AppNotification['status'], string> = {
  open: 'Abierto', dismissed: 'Descartado', resolved: 'Resuelto',
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : '—'
}

export function Notifications({ onOpen, onChanged }: { onOpen: (link: string) => void; onChanged: () => void }) {
  const [scope, setScope] = useState<Scope>('open')
  const [severity, setSeverity] = useState<NotificationSeverity | ''>('')
  const [data, setData] = useState<NotificationList | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api<NotificationList>(`/api/notifications?status=${scope}&limit=200`))
      setError('')
    } catch (err) { setError((err as Error).message) }
  }, [scope])
  useEffect(() => { setData(null); void load(); const timer = window.setInterval(() => void load(), 15000); return () => clearInterval(timer) }, [load])

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    try { await fn(); await load(); onChanged() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  const markRead = (ids?: number[]) => run(() => api('/api/notifications/read', { method: 'POST', body: JSON.stringify(ids ? { ids } : {}) }))
  const dismiss = (id: number) => run(() => api(`/api/notifications/${id}/dismiss`, { method: 'POST' }))
  async function open(item: AppNotification) {
    if (!item.read) await markRead([item.id])
    if (item.link) onOpen(item.link)
  }

  const items = (data?.items || []).filter(item => !severity || item.severity === severity)
  const counts = data?.counts

  return <div className="grid gap-5">
    <Card className="flex flex-wrap items-end justify-between gap-4 p-5">
      <div className="grid gap-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-primary">SIN LEER</p>
        <p className="text-sm text-muted-foreground">
          {counts
            ? counts.unread
              ? `${counts.unread} aviso${counts.unread === 1 ? '' : 's'}: ${counts.by_severity.critical} crítico${counts.by_severity.critical === 1 ? '' : 's'}, ${counts.by_severity.action} que requiere${counts.by_severity.action === 1 ? '' : 'n'} acción y ${counts.by_severity.info} informativo${counts.by_severity.info === 1 ? '' : 's'}.`
              : 'Todo leído.'
            : 'Cargando…'}
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid min-w-40 gap-1 text-xs font-semibold text-muted-foreground">Mostrar
          <Select value={scope} onChange={event => setScope(event.target.value as Scope)}>
            <option value="open">Abiertos</option>
            <option value="all">Historial completo</option>
          </Select>
        </label>
        <label className="grid min-w-44 gap-1 text-xs font-semibold text-muted-foreground">Gravedad
          <Select value={severity} onChange={event => setSeverity(event.target.value as NotificationSeverity | '')}>
            <option value="">Todas</option>
            {(Object.keys(severities) as NotificationSeverity[]).map(key => <option key={key} value={key}>{severities[key].label}</option>)}
          </Select>
        </label>
        <Button type="button" variant="outline" disabled={busy || !counts?.unread} onClick={() => void markRead()}>Marcar todo como leído</Button>
      </div>
    </Card>

    {error && <Alert variant="destructive">{error}</Alert>}

    {!data
      ? <div className="grid gap-3">{[0, 1, 2].map(key => <Skeleton key={key} className="h-24 w-full rounded-xl" />)}</div>
      : !items.length
        ? <Card className="grid min-h-40 place-items-center gap-2 p-8 text-center">
          <CircleCheck className="text-success-ink" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">{scope === 'open' ? 'No hay avisos abiertos. Todo funciona con normalidad.' : 'No hay avisos en el historial.'}</p>
        </Card>
        : <ul className="grid gap-3" aria-label="Avisos">
          {items.map(item => {
            const meta = severities[item.severity] || severities.info
            const Icon = meta.icon
            const unread = !item.read && item.status === 'open'
            return <li key={item.id}>
              <Card className={`flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:p-5 ${unread ? 'border-l-4 border-l-primary' : ''} ${item.status !== 'open' ? 'opacity-75' : ''}`}>
                <span className={`grid size-9 shrink-0 place-items-center rounded-full ${meta.tone}`}><Icon size={18} aria-hidden="true" /></span>
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <Badge className={meta.tone}>{meta.label}</Badge>
                    <span className="text-xs text-muted-foreground">{item.label}</span>
                    {unread && <Badge className="bg-primary text-primary-foreground">Nuevo</Badge>}
                    {item.status !== 'open' && <Badge className="bg-muted text-muted-foreground">{statusLabels[item.status]}</Badge>}
                  </div>
                  <h2 className="text-base font-semibold leading-6">{item.title}</h2>
                  {item.body && <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.body}</p>}
                  <p className="mt-2 text-xs text-muted-foreground">
                    Desde {dateTime(item.created_at)}
                    {item.last_seen_at !== item.created_at && ` · última vez ${dateTime(item.last_seen_at)}`}
                    {item.occurrences > 1 && ` · ${item.occurrences} veces`}
                    {item.resolved_at && ` · cerrado ${dateTime(item.resolved_at)}`}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col sm:items-stretch">
                  {item.link && <Button type="button" size="sm" disabled={busy} onClick={() => void open(item)}>Abrir</Button>}
                  {unread && <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void markRead([item.id])}>Marcar leído</Button>}
                  {item.status === 'open' && <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void dismiss(item.id)}>Descartar</Button>}
                </div>
              </Card>
            </li>
          })}
        </ul>}
  </div>
}
