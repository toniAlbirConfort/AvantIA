import { useCallback, useEffect, useRef, useState } from 'react'
import { MessagesSquare, PenLine, Reply, Save, Send, Undo2, X } from 'lucide-react'
import { api } from './api'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { Textarea } from './components/ui/textarea'
import { EmailThread, splitAddress } from './EmailThread'
import { RichTextEditor, type RichTextEditorHandle } from './RichTextEditor'
import { htmlToText, textToHtml } from './sanitizeHtml'
import { mailboxLabel, SignatureSettingsDialog } from './SignatureSettingsDialog'
import type { Conversation, Draft, SignaturesResponse } from './types'

type Props = {
  open: boolean
  conversation: Conversation
  readOnlyMode: boolean
  onClose: () => void
  /** Called after anything is saved or sent, so the inbox can reload. */
  onChanged: () => Promise<void> | void
}

const NO_SIGNATURE = '__none__'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex min-h-11 items-center gap-3 border-b border-border px-4">
    <span className="w-14 shrink-0 text-sm text-muted-foreground">{label}</span>
    <div className="flex min-w-0 flex-1 items-center gap-2">{children}</div>
  </div>
}

export function ReplyComposerDialog({ open, conversation, readOnlyMode, onClose, onChanged }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const editorRef = useRef<RichTextEditorHandle>(null)
  const draftIdRef = useRef<number | null>(null)
  const [session, setSession] = useState(0)
  const [subject, setSubject] = useState('')
  const [cc, setCc] = useState('')
  const [showCc, setShowCc] = useState(false)
  const [plainText, setPlainText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pane, setPane] = useState<'reply' | 'thread'>('reply')
  const [signatures, setSignatures] = useState<SignaturesResponse | null>(null)
  const [signatureChoice, setSignatureChoice] = useState(NO_SIGNATURE)
  const [signatureManager, setSignatureManager] = useState(false)

  const email = conversation.channel === 'email'
  const reply = conversation.reply
  const draft = conversation.draft && conversation.draft.status !== 'superseded' ? conversation.draft : null
  const editable = ['pending', 'error'].includes(conversation.status)
  const canSend = conversation.status === 'pending' && !readOnlyMode
  const mailboxAddress = splitAddress(reply?.mailbox).address.toLowerCase()
  const recipient = reply?.to || conversation.customer_address || ''
  const availabilityChanged = Boolean(conversation.analysis?.availability_changed)

  const defaultSignature = useCallback((data: SignaturesResponse) => {
    const own = data.signatures.find(item => item.mailbox === mailboxAddress)
    const shared = data.signatures.find(item => item.mailbox === '*')
    return (own || shared)?.mailbox || NO_SIGNATURE
  }, [mailboxAddress])

  // Initialise the composer each time it opens; later inbox refreshes must not overwrite typing.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      draftIdRef.current = draft?.id ?? null
      setSubject(draft?.subject || reply?.subject || '')
      setCc(draft?.cc || '')
      setShowCc(Boolean(draft?.cc))
      setPlainText(draft?.text || '')
      setDirty(false); setError(''); setNotice(''); setPane('reply')
      setSession(value => value + 1)
      dialog.showModal()
      if (email) {
        api<SignaturesResponse>('/api/signatures').then(data => {
          setSignatures(data)
          const choice = defaultSignature(data)
          setSignatureChoice(choice)
          // A draft saved from this composer already carries the signature the operator chose.
          if (!draft?.html_body && choice !== NO_SIGNATURE) editorRef.current?.setSignature(data.signatures.find(item => item.mailbox === choice)?.html_body || null)
        }).catch(() => setSignatures({ mailboxes: ['*'], signatures: [] }))
      }
      window.setTimeout(() => editorRef.current?.focus(), 50)
    }
    if (!open && dialog.open) dialog.close()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const initialHtml = draft?.html_body || (draft?.text ? textToHtml(draft.text) : '<p><br></p>')

  function chooseSignature(value: string, data = signatures, markDirty = true) {
    setSignatureChoice(value)
    if (markDirty) setDirty(true)
    editorRef.current?.setSignature(value === NO_SIGNATURE ? null : data?.signatures.find(item => item.mailbox === value)?.html_body || null)
  }

  async function saveDraft(): Promise<Draft> {
    const html = email ? editorRef.current?.getHtml() || '' : ''
    const text = email ? htmlToText(html) : plainText.trim()
    if (!text) throw new Error('Escribe la respuesta antes de guardarla.')
    const saved = await api<Draft>(`/api/conversations/${conversation.id}/draft`, {
      method: 'PUT',
      body: JSON.stringify(email
        ? { text, html_body: html, subject: subject.trim(), cc: cc.trim(), expected_draft_id: draftIdRef.current }
        : { text, expected_draft_id: draftIdRef.current }),
    })
    draftIdRef.current = saved.id
    setDirty(false)
    return saved
  }

  async function run(task: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await task() } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  const save = () => run(async () => {
    await saveDraft()
    setNotice(`Borrador guardado a las ${new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(new Date())}.`)
    await onChanged()
  })

  const send = () => run(async () => {
    await saveDraft()
    await api(`/api/conversations/${conversation.id}/approve`, { method: 'POST' })
    await onChanged()
    onClose()
  })

  const discard = () => {
    if (!window.confirm('¿Descartar los cambios y volver al último borrador guardado?')) return
    setSubject(draft?.subject || reply?.subject || '')
    setCc(draft?.cc || ''); setPlainText(draft?.text || '')
    setDirty(false); setError(''); setNotice('')
    setSession(value => value + 1)
    if (email && signatures && !draft?.html_body) window.setTimeout(() => chooseSignature(defaultSignature(signatures), signatures, false), 0)
  }

  // Closing keeps the work, like a mail client: unsaved changes become the draft.
  const requestClose = () => {
    if (!dirty || !editable) { onClose(); return }
    void run(async () => { await saveDraft(); await onChanged(); onClose() })
  }

  const disabledReason = !editable
    ? `Esta conversación está en estado «${conversation.status}»: la respuesta no se puede modificar.`
    : ''

  return <dialog
    ref={dialogRef}
    aria-labelledby="reply-composer-title"
    onCancel={event => { if (event.target !== event.currentTarget) return; event.preventDefault(); requestClose() }}
    onClose={event => { if (event.target === event.currentTarget && open) onClose() }}
    className="m-auto h-[96vh] max-h-[96vh] w-[98vw] max-w-[1600px] overflow-hidden rounded-xl border border-border bg-background p-0 text-foreground shadow-2xl backdrop:bg-black/50"
  >
    {open && <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border bg-card px-4 py-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden="true" className="grid size-9 shrink-0 place-items-center rounded-full bg-brand-orange/10 text-brand-orange"><Reply className="size-5" /></span>
          <div className="min-w-0">
            <h2 id="reply-composer-title" className="truncate text-base font-semibold sm:text-lg">{conversation.subject || reply?.subject || 'Responder'}</h2>
            <p className="truncate text-xs text-muted-foreground">{conversation.customer_name || recipient || 'Cliente'}{conversation.customer_name && recipient ? ` · ${recipient}` : ''}</p>
          </div>
        </div>
        <Button type="button" variant="ghost" size="icon" disabled={busy} onClick={requestClose} aria-label={dirty ? 'Guardar borrador y cerrar' : 'Cerrar'} title={dirty ? 'Guardar borrador y cerrar' : 'Cerrar'}><X aria-hidden="true" /></Button>
      </header>

      <div role="tablist" aria-label="Vista" className="grid grid-cols-2 border-b border-border bg-card lg:hidden">
        {(['reply', 'thread'] as const).map(item => <button key={item} type="button" role="tab" aria-selected={pane === item} onClick={() => setPane(item)} className={`flex items-center justify-center gap-2 py-2.5 text-sm font-medium ${pane === item ? 'border-b-2 border-brand-orange text-foreground' : 'text-muted-foreground'}`}>
          {item === 'reply' ? <><Reply className="size-4" aria-hidden="true" />Respuesta</> : <><MessagesSquare className="size-4" aria-hidden="true" />Conversación ({conversation.messages?.length || 0})</>}
        </button>)}
      </div>

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <section aria-label="Conversación" className={`min-h-0 overflow-auto bg-muted/40 p-3 sm:p-4 lg:block lg:border-r lg:border-border ${pane === 'thread' ? 'block' : 'hidden'}`}>
          {conversation.messages?.length
            ? email
              ? <EmailThread messages={conversation.messages} fallbackSender={conversation.customer_address} fallbackSubject={conversation.subject} />
              : <div className="grid gap-2">{[...conversation.messages].reverse().map(message => <article key={message.id} className={`w-fit max-w-[90%] rounded-lg border border-border p-3 text-sm leading-6 ${message.direction === 'outbound' ? 'ml-auto bg-accent' : 'bg-card'}`}><p className="mb-1 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">{message.direction === 'inbound' ? 'Recibido' : 'Enviado'} · {new Date(message.created_at).toLocaleString('es-ES')}</p><p className="m-0 whitespace-pre-wrap break-words">{message.body}</p></article>)}</div>
            : <p className="p-6 text-center text-sm text-muted-foreground">No hay mensajes en esta conversación.</p>}
        </section>

        <section aria-label="Redactar respuesta" className={`min-h-0 flex-col bg-card lg:flex ${pane === 'reply' ? 'flex' : 'hidden'}`}>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
            <Button type="button" disabled={busy || !canSend} title={readOnlyMode ? 'El envío está desactivado en modo solo lectura' : undefined} onClick={() => void send()}>
              <Send aria-hidden="true" />{readOnlyMode ? 'Envío bloqueado' : busy ? 'Enviando…' : 'Enviar'}
            </Button>
            <Button type="button" variant="secondary" disabled={busy || !editable} onClick={() => void save()}><Save aria-hidden="true" />Guardar borrador</Button>
            <Button type="button" variant="ghost" disabled={busy || !dirty} onClick={discard}><Undo2 aria-hidden="true" />Descartar cambios</Button>
            <span aria-live="polite" className="ml-auto text-xs text-muted-foreground">{busy ? 'Guardando…' : notice || (dirty ? 'Cambios sin guardar' : '')}</span>
          </div>

          {(disabledReason || error || availabilityChanged || draft?.error) && <div className="grid gap-2 border-b border-border px-4 py-3">
            {disabledReason && <Alert variant="info">{disabledReason}</Alert>}
            {availabilityChanged && <Alert variant="warning">La disponibilidad cambió desde que se preparó el borrador. Revísalo antes de enviar.</Alert>}
            {draft?.error && <Alert variant="destructive">{draft.error}</Alert>}
            {error && <Alert variant="destructive" role="alert">{error}</Alert>}
          </div>}

          {email ? <>
            {reply?.mailbox && <Field label="De"><span className="truncate text-sm">{reply.mailbox}</span></Field>}
            <Field label="Para">
              <span className="inline-flex min-w-0 max-w-full items-center rounded-full bg-accent px-2.5 py-0.5 text-sm"><span className="truncate">{recipient || '—'}</span></span>
              {!showCc && <button type="button" className="ml-auto shrink-0 text-sm text-primary hover:underline" onClick={() => setShowCc(true)}>CC</button>}
            </Field>
            {showCc && <Field label="CC"><input aria-label="Con copia" className="h-10 min-w-0 flex-1 bg-transparent text-sm outline-none" value={cc} disabled={!editable} placeholder="correo@ejemplo.com, otra@ejemplo.com" onChange={event => { setCc(event.target.value); setDirty(true) }} /></Field>}
            <Field label="Asunto"><input aria-label="Asunto" className="h-10 min-w-0 flex-1 bg-transparent text-sm font-medium outline-none" value={subject} disabled={!editable} onChange={event => { setSubject(event.target.value); setDirty(true) }} /></Field>
            <RichTextEditor
              ref={editorRef}
              className="min-h-72 flex-1"
              initialHtml={initialHtml}
              resetKey={`${conversation.id}:${session}`}
              ariaLabel="Cuerpo de la respuesta"
              placeholder="Escribe tu respuesta…"
              disabled={!editable}
              onChange={() => setDirty(true)}
              toolbarEnd={<label className="flex items-center gap-1.5 text-xs text-muted-foreground"><PenLine aria-hidden="true" className="size-3.5" /><span className="sr-only sm:not-sr-only">Firma</span>
                <select aria-label="Firma" className="h-8 max-w-44 rounded-md border border-input bg-card px-1.5 text-xs text-foreground" value={signatureChoice} disabled={!editable || !signatures} onChange={event => event.target.value === '__manage__' ? setSignatureManager(true) : chooseSignature(event.target.value)}>
                  <option value={NO_SIGNATURE}>Sin firma</option>
                  {signatures?.signatures.map(item => <option key={item.mailbox} value={item.mailbox}>{mailboxLabel(item.mailbox)}</option>)}
                  <option value="__manage__">Gestionar firmas…</option>
                </select>
              </label>}
            />
          </> : <div className="flex min-h-0 flex-1 flex-col gap-2 p-4">
            <label htmlFor="whatsapp-reply" className="text-sm font-medium">Mensaje de WhatsApp para {recipient || 'el cliente'}</label>
            <Textarea id="whatsapp-reply" className="min-h-64 flex-1 text-[15px] leading-relaxed" value={plainText} disabled={!editable} onChange={event => { setPlainText(event.target.value); setDirty(true) }} placeholder="Escribe tu respuesta…" />
          </div>}
        </section>
      </div>
    </div>}
    {email && <SignatureSettingsDialog
      open={signatureManager}
      data={signatures}
      initialMailbox={signatureChoice !== NO_SIGNATURE ? signatureChoice : mailboxAddress}
      onClose={() => setSignatureManager(false)}
      onSaved={(next, mailbox) => {
        setSignatures(next)
        const updated = next.signatures.find(item => item.mailbox === mailbox)
        if (signatureChoice === mailbox) chooseSignature(updated ? mailbox : NO_SIGNATURE, next)
        else if (signatureChoice === NO_SIGNATURE && updated && mailbox === defaultSignature(next)) chooseSignature(mailbox, next)
      }}
    />}
  </dialog>
}
