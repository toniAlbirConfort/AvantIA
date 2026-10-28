import { useState } from 'react'
import { Languages, Maximize2 } from 'lucide-react'
import { api } from './api'
import { Button } from './components/ui/button'
import { emailDocument } from './EmailPreviewDialog'
import type { EmailTranslation, Message } from './types'

type Props = {
  message: Message
  conversationId: number
  fallbackSender?: string | null
  fallbackSubject?: string | null
  onExpand: (message: Message) => void
}

export function EmailMessageContent({
  message, conversationId, fallbackSender, fallbackSubject, onExpand,
}: Props) {
  const [translation, setTranslation] = useState<EmailTranslation | null>(null)
  const [showTranslation, setShowTranslation] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const originalSubject = message.subject || fallbackSubject || '(sin asunto)'

  async function translate() {
    setBusy(true)
    setError('')
    try {
      const result = await api<EmailTranslation>(
        '/api/conversations/' + conversationId + '/messages/' + message.id + '/translate',
        { method: 'POST' },
      )
      setTranslation(result)
      setShowTranslation(true)
    } catch (err) {
      setError((err as Error).message || 'No se pudo traducir el email')
    } finally {
      setBusy(false)
    }
  }

  return <>
    <dl className="grid gap-2 border-b border-border px-4 pb-4 text-xs sm:grid-cols-[4rem_minmax(0,1fr)]">
      <dt className="text-muted-foreground">De</dt>
      <dd className="m-0 break-all">{message.sender || fallbackSender || '—'}</dd>
      {message.recipient && <>
        <dt className="text-muted-foreground">Para</dt>
        <dd className="m-0 break-all">{message.recipient}</dd>
      </>}
      <dt className="text-muted-foreground">Asunto</dt>
      <dd className="m-0 break-all">{originalSubject}</dd>
    </dl>
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
      <Button type="button" variant="secondary" size="sm" onClick={() => onExpand(message)}>
        <Maximize2 size={15} />Ampliar correo
      </Button>
      {message.direction === 'inbound' && <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={busy}
        aria-live="polite"
        onClick={() => translation ? setShowTranslation(value => !value) : void translate()}
      >
        <Languages size={15} />
        {busy ? 'Traduciendo…' : translation ? (showTranslation ? 'Ver original' : 'Ver traducción') : 'Traducir al español'}
      </Button>}
      {error && <span role="alert" className="flex flex-wrap items-center gap-1 text-xs text-error-ink">
        {error}
        <Button type="button" variant="link" size="sm" className="h-auto px-1 py-0" disabled={busy} onClick={() => void translate()}>
          Reintentar
        </Button>
      </span>}
    </div>
    {showTranslation && translation
      ? <div className="whitespace-pre-wrap break-words p-4" lang="es">{translation.body || '(correo sin texto)'}</div>
      : message.html_body
        ? <iframe className="block h-[50vh] min-h-[20rem] w-full border-0 bg-white" title={'Cuerpo del email: ' + originalSubject} sandbox="" referrerPolicy="no-referrer" srcDoc={emailDocument(message.html_body)} />
        : <div className="whitespace-pre-wrap break-words p-4">{message.body || '(correo sin texto)'}</div>}
  </>
}
