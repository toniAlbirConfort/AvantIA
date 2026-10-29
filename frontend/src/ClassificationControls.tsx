import type { MessageClassification } from './types'
import { Badge as UiBadge } from './components/ui/badge'
import { Select } from './components/ui/select'

const names: Record<string, string> = {
  demand: 'Demanda',
  guest_service: 'Atención de reserva',
  non_demand: 'No demanda',
  uncertain: 'Sin determinar',
  review: 'Revisión',
  unclassified: 'Sin clasificar',
  high: 'Confianza alta',
  medium: 'Confianza media',
  low: 'Confianza baja',
}

const styles: Record<string, string> = {
  demand: 'bg-success-bg text-success-ink',
  guest_service: 'bg-info-bg text-info-ink',
  non_demand: 'bg-muted text-muted-foreground',
  uncertain: 'bg-warning-bg text-warning-ink',
  review: 'bg-warning-bg text-warning-ink',
  unclassified: 'bg-muted text-muted-foreground',
  high: 'bg-success-bg text-success-ink',
  medium: 'bg-warning-bg text-warning-ink',
  low: 'bg-warning-bg text-warning-ink',
}

function ClassificationBadge({ value }: { value: string }) {
  return <UiBadge className={styles[value] || 'bg-muted text-muted-foreground'}>{names[value] || value}</UiBadge>
}

export function MessageClassificationControl({
  classification,
  disabled,
  onChange,
}: {
  classification?: MessageClassification | null
  disabled: boolean
  onChange: (label: 'demand' | 'guest_service' | 'non_demand') => void
}) {
  const current = classification?.needs_review && !classification.reviewed_at
    ? ''
    : classification?.label || ''
  const displayed = current || 'unclassified'
  return <div className="mt-3 grid gap-2 rounded-md border border-border bg-background p-3 sm:flex sm:items-center sm:justify-between">
    <div className="grid min-w-0 gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <ClassificationBadge value={displayed} />
        {classification?.needs_review && <ClassificationBadge value="review" />}
        {classification?.confidence && <ClassificationBadge value={classification.confidence} />}
        {classification?.reviewed_at && <span className="text-xs text-muted-foreground">Revisada por un operador</span>}
      </div>
      {classification?.reason && <p className="m-0 text-xs leading-5 text-muted-foreground">{classification.reason}</p>}
      {classification?.predicted_label && classification.predicted_label !== classification.label && (
        <p className="m-0 text-xs leading-5 text-muted-foreground">
          Predicción original: {names[classification.predicted_label] || classification.predicted_label}
        </p>
      )}
    </div>
    <label className="grid shrink-0 gap-1 text-xs font-medium sm:w-52">
      Corregir clasificación
      <Select
        className="h-9"
        aria-label="Corregir clasificación del mensaje"
        value={current}
        disabled={disabled}
        onChange={event => {
          const value = event.target.value
          if (value === 'demand' || value === 'guest_service' || value === 'non_demand') onChange(value)
        }}
      >
        <option value="">{classification?.needs_review ? 'Selecciona tras revisar' : 'Sin clasificar'}</option>
        <option value="demand">Demanda</option>
        <option value="guest_service">Atención de reserva</option>
        <option value="non_demand">No demanda</option>
      </Select>
    </label>
  </div>
}
