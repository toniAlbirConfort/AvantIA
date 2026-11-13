import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'

export type RegistrationAlertStatus = {
  mode: 'off' | 'shadow'
  delay_minutes: number
  open_alerts: number
  last_7_days: {
    checked: number; registered: number; alerted: number; pending: number; excluded: number
    registered_after_alert: number; linked_by_operator: number; dismissed_by_operator: number
  }
  last_sync_at: string | null
  sync_interval_hours: number
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : 'nunca'
}

export function RegistrationAlertCard({ value, onOpenAlerts }: { value: RegistrationAlertStatus; onOpenAlerts: () => void }) {
  const week = value.last_7_days
  const consultations = week.registered + week.alerted + week.pending
  const stats: [string, number][] = [
    ['Consultas revisadas', consultations],
    ['Registradas en Avantio', week.registered],
    ['Sin registrar', week.alerted],
    ['Registradas tras el aviso', week.registered_after_alert],
    ['Vinculadas a mano', week.linked_by_operator],
    ['Marcadas «no procede»', week.dismissed_by_operator],
  ]
  return <Card className="p-5">
    <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">CONTROL</p>
    <h2 className="mb-3 text-xl font-semibold">Consultas sin registrar en Avantio</h2>
    <Badge className={value.mode === 'shadow' ? 'bg-info-bg text-info-ink' : 'bg-muted text-muted-foreground'}>{value.mode === 'shadow' ? 'Modo sombra' : 'Desactivada'}</Badge>
    <p className="my-4 text-sm leading-6 text-muted-foreground">
      {value.mode === 'shadow'
        ? `Tras cada sincronización con Avantio (cada ${value.sync_interval_hours} h) marca las consultas por email que, pasados ${value.delay_minutes} minutos de horario de oficina, no tienen reserva. Aún no avisa a nadie.`
        : 'Desactivada. Se activa con REGISTRATION_ALERT_MODE en el .env del servidor.'}
    </p>
    <dl className="mb-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
      {stats.map(([name, total]) => <div key={name}><dt className="text-xs text-muted-foreground">{name}</dt><dd className="font-semibold">{total}</dd></div>)}
    </dl>
    <p className="mb-3 text-xs text-muted-foreground">Últimos 7 días · última sincronización correcta: {dateTime(value.last_sync_at)}</p>
    <Button type="button" variant={value.open_alerts ? 'default' : 'outline'} onClick={onOpenAlerts}>
      Ver avisos abiertos ({value.open_alerts})
    </Button>
  </Card>
}
