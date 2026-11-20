import { useCallback, useEffect, useState } from 'react'
import { Building2, RefreshCw, ShieldAlert, UserRoundSearch, Users } from 'lucide-react'
import { api } from './api'
import { CustomerPanel } from './Customers'
import { Status } from './Reservations'
import { reasonText } from './RegistrationPanel'
import type { BookedForOthersCustomer, BookedForOthersResponse, CustomerSummary } from './types'
import { Alert } from './components/ui/alert'
import { Badge } from './components/ui/badge'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { Skeleton } from './components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './components/ui/table'

const euros = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })
const integer = new Intl.NumberFormat('es-ES', { useGrouping: 'always' } as unknown as Intl.NumberFormatOptions)
const checkStatus: Record<string, string> = {
  pending: 'Pendiente', alerted: 'Avisada', registered: 'Registrada', excluded: 'Descartada', dismissed: 'Descartada a mano',
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T12:00:00Z`))
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat('es-ES', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

function Metric({ label, value, detail }: { label: string; value: number; detail: string }) {
  return <Card className="p-4">
    <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
    <strong className="mt-1 block text-3xl font-semibold tabular-nums">{integer.format(value)}</strong>
    <small className="text-xs text-muted-foreground">{detail}</small>
  </Card>
}

function SectionTitle({ icon: Icon, title, count, children }: { icon: typeof Users; title: string; count: number; children: React.ReactNode }) {
  return <div className="grid gap-1">
    <h2 className="flex items-center gap-2 text-lg font-semibold"><Icon size={18} aria-hidden="true" className="text-primary" />{title}<Badge className="bg-muted font-medium text-muted-foreground">{count}</Badge></h2>
    <p className="max-w-4xl text-sm leading-6 text-muted-foreground">{children}</p>
  </div>
}

// Each reservation made for someone else: who stays, when, and in which state it is.
function BookingsTable({ customers, onOpen }: { customers: BookedForOthersCustomer[]; onOpen: (item: BookedForOthersCustomer) => void }) {
  return <div className="overflow-x-auto rounded-md border border-border bg-card">
    <Table className="min-w-[52rem]">
      <TableHeader><TableRow>
        <TableHead>Cliente que reserva</TableHead><TableHead>Ocupante</TableHead><TableHead>Entrada – salida</TableHead><TableHead>Localizador</TableHead><TableHead>Canal</TableHead><TableHead>Estado</TableHead><TableHead className="text-right">Importe</TableHead>
      </TableRow></TableHeader>
      <TableBody>{customers.flatMap(item => item.reservations.map((booking, index) => <TableRow key={booking.locator}>
        <TableCell className="align-top">{index === 0
          ? <Button type="button" variant="link" className="h-auto p-0 text-left font-semibold" onClick={() => onOpen(item)}>{item.name}</Button>
          : <span className="sr-only">{item.name}</span>}</TableCell>
        <TableCell>{booking.occupant}</TableCell>
        <TableCell className="whitespace-nowrap">{shortDate(booking.start_date)} – {shortDate(booking.end_date)}</TableCell>
        <TableCell className="whitespace-nowrap font-mono text-xs">{booking.locator}</TableCell>
        <TableCell className="max-w-48 truncate" title={booking.portal || undefined}>{booking.portal || '—'}</TableCell>
        <TableCell><Status value={booking.status} /></TableCell>
        <TableCell className="text-right tabular-nums">{euros.format(Number(booking.total))}</TableCell>
      </TableRow>))}</TableBody>
    </Table>
  </div>
}

export function CustomerAudit({ onImport }: { onImport: () => void }) {
  const [data, setData] = useState<BookedForOthersResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [selected, setSelected] = useState<CustomerSummary | null>(null)
  const closePanel = useCallback(() => setSelected(null), [])

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    api<BookedForOthersResponse>('/api/customers/audit/booked-for-others')
      .then(result => { if (active) setData(result) })
      .catch(err => { if (active) setError((err as Error).message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [retryKey])

  function open(item: BookedForOthersCustomer) {
    setSelected({ avantio_id: item.avantio_id, display_name: item.name, email: null, phone: null, country: null, document_number: null, customer_class: item.customer_class })
  }

  const summary = data?.summary
  return <div className="grid gap-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold leading-tight sm:text-3xl">Clientes que reservan para otros</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Auditoría de las reservas en las que el cliente que reserva en Avantio no es quien se aloja, y de los clientes que nunca figuran como ocupantes.</p>
      </div>
      <Button type="button" variant="ghost" size="icon" onClick={() => setRetryKey(value => value + 1)} disabled={loading} aria-label="Actualizar auditoría"><RefreshCw size={16} className={loading ? 'animate-spin motion-reduce:animate-none' : ''} /></Button>
    </div>

    {error && <Alert variant="destructive" className="flex items-center justify-between gap-4"><span>{error}</span><Button type="button" variant="outline" size="sm" onClick={() => setRetryKey(value => value + 1)}>Reintentar</Button></Alert>}
    {loading && !data && <div className="grid gap-2" role="status" aria-label="Cargando auditoría">{Array.from({ length: 5 }, (_, index) => <Skeleton key={index} className="h-20 w-full rounded-md" />)}</div>}

    {data && !data.has_snapshot && <Card className="grid justify-items-center gap-3 px-5 py-12 text-center">
      <h2 className="text-lg font-semibold">Aún no hay reservas importadas</h2>
      <p className="max-w-lg text-sm leading-6 text-muted-foreground">La auditoría se calcula con el listado de reservas de Avantio y la ficha de cada cliente.</p>
      <Button type="button" variant="outline" onClick={onImport}>Ir a Importaciones</Button>
    </Card>}

    {data && summary && data.has_snapshot && <>
      <Alert variant="info" className="grid gap-1.5">
        <strong className="flex items-center gap-2 font-semibold"><ShieldAlert size={16} aria-hidden="true" />Por qué importa</strong>
        <span className="text-sm leading-6">La supervisión de consultas sin registrar en Avantio da por huésped a quien tiene una reserva y por registrada una consulta cuyo email aparece en una reserva reciente. Un cliente que solo reservó para otra persona (un familiar o un amigo) cumple esas condiciones sin haberse alojado nunca: si vuelve a escribir pidiendo alojamiento y nadie registra su petición, la app podría no avisar.</span>
      </Alert>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="Reservas para otra persona" value={summary.for_others_direct} detail={`Canal directo · de ${integer.format(summary.with_customer)} con ficha de cliente`} />
        <Metric label="Nunca se alojan" value={summary.customers_never_stay} detail={`${summary.single_reservation} con una sola reserva`} />
        <Metric label="También se alojan" value={summary.customers_also_stay} detail="Reservan para otros y para sí" />
        <Metric label="Consultas afectadas" value={summary.consultations} detail="De quienes nunca se alojan" />
      </div>

      <section className="grid gap-3" aria-labelledby="never-stay">
        <div id="never-stay"><SectionTitle icon={UserRoundSearch} title="Nunca figuran como ocupantes" count={data.never_stay.length}>
          Particulares que reservaron para otra persona y no aparecen como ocupantes en ninguna de sus reservas. Son el caso que la supervisión podría pasar por alto.
        </SectionTitle></div>
        {data.never_stay.length === 0
          ? <Card className="px-5 py-8 text-center text-sm text-muted-foreground">Ningún cliente cumple este criterio.</Card>
          : <>
            <BookingsTable customers={data.never_stay} onOpen={open} />
            {data.never_stay.some(item => item.consultations?.length)
              ? <Card className="grid gap-2 p-4">
                <h3 className="text-sm font-semibold">Consultas de estos clientes revisadas por la supervisión</h3>
                <ul className="grid gap-1.5 text-sm">{data.never_stay.flatMap(item => (item.consultations || []).map(check => <li key={check.id} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{item.name}</span>
                  <span className="text-muted-foreground">{dateTime(check.received_at)}</span>
                  <Badge className={check.status === 'excluded' || check.status === 'registered' ? 'bg-warning-bg text-warning-ink' : 'bg-muted text-muted-foreground'}>{checkStatus[check.status] || check.status}</Badge>
                  {check.reason && <span className="text-muted-foreground">{reasonText(check.reason)}</span>}
                  <a className="text-primary underline-offset-4 hover:underline" href={`/inbox/${check.conversation_id}`}>Ver consulta</a>
                </li>))}</ul>
              </Card>
              : <p className="text-sm text-muted-foreground">Ninguno de ellos ha escrito desde que funciona la supervisión de consultas sin registrar, así que de momento no se ha dejado de avisar por este motivo.</p>}
          </>}
      </section>

      <section className="grid gap-3" aria-labelledby="also-stay">
        <div id="also-stay"><SectionTitle icon={Users} title="Reservan para otros y también se alojan" count={data.also_stay.length}>
          Particulares que en alguna reserva figuran como ocupantes, sea en la suya o en la de otra persona. Para la supervisión son huéspedes reales; se listan como contexto.
        </SectionTitle></div>
        {data.also_stay.length > 0 && <BookingsTable customers={data.also_stay} onOpen={open} />}
      </section>

      <section className="grid gap-3" aria-labelledby="companies">
        <div id="companies"><SectionTitle icon={Building2} title="Empresas, agencias y organismos" count={data.companies.length}>
          Clientes que, por su nombre, son una organización que reserva para su gente por canal directo. Es su forma normal de trabajar y no afecta a la supervisión.
        </SectionTitle></div>
        {data.companies.length > 0 && <BookingsTable customers={data.companies} onOpen={open} />}
      </section>

      <Card className="grid gap-2 p-4 text-xs leading-5 text-muted-foreground">
        <h2 className="text-sm font-semibold text-foreground">Cómo se calcula</h2>
        <p>Se revisan {integer.format(summary.reservations)} reservas de la última importación de Avantio ({dateTime(data.snapshot_at)}), sin contar el uso de propietario ni los bloqueos. Una reserva es «para otra persona» cuando ninguno de los apellidos del cliente aparece en el nombre del ocupante y sus emails no coinciden. Los nombres se comparan admitiendo las grafías de Avantio (Sørensen, SOERENSEN y Sorensen cuentan como el mismo apellido).</p>
        <p>Solo cuenta el canal directo: en portales y agencias el cliente es el intermediario, y así ocurre en {integer.format(summary.for_others_intermediaries)} reservas más. Las empresas se reconocen por su forma jurídica o por palabras como «Viajes», «Club» o «Ayuntamiento». Un cliente «nunca se aloja» si no figura como ocupante en ninguna reserva, de cualquier canal o estado, ni por nombre ni por email.</p>
        <p>Calculado el {dateTime(data.generated_at)}. Es una comprobación automática: conviene revisar cada caso antes de sacar conclusiones.</p>
      </Card>
    </>}

    {selected && <CustomerPanel summary={selected} onClose={closePanel} />}
  </div>
}
