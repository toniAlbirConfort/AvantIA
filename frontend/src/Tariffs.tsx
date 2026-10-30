import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { TariffGroup } from './types'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Select } from './components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

function displayDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${day}/${month}/${year}`
}

function displayPrice(value: string) {
  return new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(Number(value))
}

export function Tariffs({ refreshVersion }: { refreshVersion: number }) {
  const [groups, setGroups] = useState<TariffGroup[] | null>(null)
  const [selectedName, setSelectedName] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api<TariffGroup[]>('/api/tariffs')
      setGroups(result)
      setSelectedName(current => result.some(group => group.name === current) ? current : result[0]?.name || '')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load, refreshVersion])

  if (loading) {
    return <Card className="p-5 text-sm text-muted-foreground" role="status">Cargando tarifas…</Card>
  }

  if (error) {
    return <Card className="grid gap-4 p-5 sm:p-6">
      <Alert variant="destructive" role="alert">No se pudieron cargar las tarifas: {error}</Alert>
      <Button type="button" variant="secondary" className="w-fit" onClick={() => void load()}>Reintentar</Button>
    </Card>
  }

  const selected = groups?.find(group => group.name === selectedName)
  if (!groups?.length) {
    return <Card className="p-5 text-sm text-muted-foreground">Todavía no hay tarifas cargadas.</Card>
  }

  return <Card className="overflow-hidden p-4 sm:p-6">
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">TEMPORADAS · TARIFA SA</p>
        <h2 className="text-xl font-semibold">Tarifas por alojamiento</h2>
      </div>
      {selected && <p className="text-sm text-muted-foreground" aria-live="polite">
        {selected.seasons.length} temporadas · precio por noche
      </p>}
    </div>
    <label className="mb-5 grid max-w-xl gap-1.5 text-sm font-medium">
      Grupo de tarifa
      <Select value={selectedName} onChange={event => setSelectedName(event.target.value)}>
        {groups.map(group => <option key={group.name} value={group.name}>{group.name}</option>)}
      </Select>
    </label>
    {selected && <div className="max-w-full overflow-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fecha inicio</TableHead>
            <TableHead>Última noche</TableHead>
            <TableHead className="text-right">SA · Precio/noche</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {selected.seasons.map(season => <TableRow key={`${season.rate_plan}-${season.start_date}`}>
            <TableCell className="whitespace-nowrap tabular-nums">{displayDate(season.start_date)}</TableCell>
            <TableCell className="whitespace-nowrap tabular-nums">{displayDate(season.end_date)}</TableCell>
            <TableCell className="text-right font-medium tabular-nums">{displayPrice(season.nightly_rate)}</TableCell>
          </TableRow>)}
        </TableBody>
      </Table>
    </div>}
    <p className="mt-3 text-xs leading-5 text-muted-foreground">Ambas fechas están incluidas en la temporada. Las tarifas se muestran para consulta y no se incorporan a las respuestas.</p>
  </Card>
}
