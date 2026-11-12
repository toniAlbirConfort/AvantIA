import { Mail, Phone, UserRound } from 'lucide-react'
import type { ContactInfo, InboxThread } from './types'
import { Badge } from './components/ui/badge'

export function threadTitle(item: Pick<InboxThread, 'customer_name' | 'customer_address' | 'contact'>) {
  const contact = item.contact
  if (contact?.source === 'relayed') return contact.name || contact.emails[0] || contact.phones[0] || 'Lead de un compañero'
  return item.customer_name || item.customer_address || 'Cliente'
}

export function CustomerContactCard({ contact }: { contact: ContactInfo }) {
  const relayed = contact.source === 'relayed'
  const empty = !contact.emails.length && !contact.phones.length
  return <div className="grid gap-2 border-b border-border bg-background px-4 py-3 text-sm sm:px-6" aria-label="Contacto del cliente">
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-semibold uppercase tracking-wide text-primary">Contacto del cliente</span>
      {relayed && <Badge className="bg-info-bg text-info-ink">Vía compañero{contact.relayed_by ? ` · ${contact.relayed_by}` : ''}</Badge>}
      {contact.wants_price
        ? <Badge className="bg-warning-bg text-warning-ink">Pide precio</Badge>
        : <Badge className="bg-muted text-muted-foreground">Solo disponibilidad</Badge>}
      <span className="text-xs text-muted-foreground">{contact.verified ? 'Confirmado por un operador' : 'Detectado automáticamente'}</span>
    </div>
    <div className="flex flex-wrap gap-x-5 gap-y-1">
      {contact.name && <span className="flex items-center gap-1.5"><UserRound size={14} aria-hidden="true" className="text-muted-foreground" />{contact.name}</span>}
      {contact.emails.map(email => <a key={email} className="flex items-center gap-1.5 text-primary underline-offset-4 hover:underline" href={`mailto:${email}`}><Mail size={14} aria-hidden="true" />{email}</a>)}
      {contact.phones.map(phone => <a key={phone} className="flex items-center gap-1.5 text-primary underline-offset-4 hover:underline" href={`tel:${phone}`}><Phone size={14} aria-hidden="true" />{phone}</a>)}
      {empty && <span className="text-muted-foreground">No se ha encontrado email ni teléfono en el mensaje.</span>}
    </div>
    {relayed && <p className="m-0 text-xs leading-5 text-muted-foreground">El email lo envió un compañero: la respuesta debe ir a estos datos, no al remitente.</p>}
  </div>
}
