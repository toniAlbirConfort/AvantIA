import { useEffect, useRef, useState } from 'react'
import { Check, PenLine, X } from 'lucide-react'
import { api } from './api'
import { Alert } from './components/ui/alert'
import { Button } from './components/ui/button'
import { RichTextEditor, type RichTextEditorHandle } from './RichTextEditor'
import { htmlToText } from './sanitizeHtml'
import type { SignaturesResponse } from './types'

export const mailboxLabel = (mailbox: string) => mailbox === '*' ? 'Todas las cuentas' : mailbox

type Props = {
  open: boolean
  data: SignaturesResponse | null
  initialMailbox?: string
  onClose: () => void
  onSaved: (data: SignaturesResponse, mailbox: string) => void
}

export function SignatureSettingsDialog({ open, data, initialMailbox, onClose, onSaved }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const editorRef = useRef<RichTextEditorHandle>(null)
  const [mailbox, setMailbox] = useState('*')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) {
      setMailbox(initialMailbox && data?.mailboxes.includes(initialMailbox) ? initialMailbox : data?.mailboxes[0] || '*')
      setDirty(false); setError(''); setSaved(false)
      dialog.showModal()
    }
    if (!open && dialog.open) dialog.close()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const current = data?.signatures.find(item => item.mailbox === mailbox)

  function choose(next: string) {
    if (next === mailbox) return
    if (dirty && !window.confirm('Hay cambios sin guardar en esta firma. ¿Descartarlos?')) return
    setMailbox(next); setDirty(false); setError(''); setSaved(false)
  }

  async function save(remove = false) {
    const html = remove ? '' : editorRef.current?.getHtml() || ''
    setBusy(true); setError(''); setSaved(false)
    try {
      const body = remove || !htmlToText(html) && !html.includes('<img') ? '' : html
      const next = await api<SignaturesResponse>('/api/signatures', { method: 'PUT', body: JSON.stringify({ mailbox, html_body: body }) })
      setDirty(false); setSaved(true)
      onSaved(next, mailbox)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  function requestClose() {
    if (dirty && !window.confirm('Hay cambios sin guardar en esta firma. ¿Cerrar igualmente?')) return
    onClose()
  }

  return <dialog
    ref={dialogRef}
    aria-labelledby="signature-settings-title"
    onCancel={event => { if (event.target !== event.currentTarget) return; event.preventDefault(); requestClose() }}
    onClose={event => { if (event.target === event.currentTarget && open) onClose() }}
    className="m-auto max-h-[90vh] w-[96vw] max-w-4xl overflow-hidden rounded-xl border border-border bg-card p-0 text-foreground shadow-2xl backdrop:bg-black/40"
  >
    {open && data && <div className="flex max-h-[90vh] flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div className="flex items-center gap-3"><PenLine aria-hidden="true" className="size-5 text-primary" /><div><h2 id="signature-settings-title" className="text-lg font-semibold">Mis firmas</h2><p className="text-xs text-muted-foreground">Solo tú ves y usas tus firmas. Se añaden al final de tus respuestas.</p></div></div>
        <Button type="button" variant="ghost" size="icon" onClick={requestClose} aria-label="Cerrar firmas"><X aria-hidden="true" /></Button>
      </header>
      <div className="grid min-h-0 flex-1 md:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="Cuentas de correo" className="border-b border-border p-3 md:border-b-0 md:border-r">
          <p className="mb-2 px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Cuenta</p>
          <ul className="grid gap-1">{data.mailboxes.map(item => {
            const has = data.signatures.some(signature => signature.mailbox === item)
            return <li key={item}><button type="button" aria-current={item === mailbox ? 'true' : undefined} onClick={() => choose(item)} className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left text-sm ${item === mailbox ? 'bg-accent font-semibold' : 'hover:bg-accent/60'}`}>
              <span className="min-w-0 truncate">{mailboxLabel(item)}</span>
              {has && <Check aria-label="Firma configurada" className="size-4 shrink-0 text-success-ink" />}
            </button></li>
          })}</ul>
          <p className="mt-3 px-2 text-xs leading-5 text-muted-foreground">La firma de una cuenta concreta tiene prioridad sobre la de «Todas las cuentas».</p>
        </nav>
        <div className="flex min-h-0 flex-col p-4">
          <p className="mb-2 text-sm font-medium">Firma para <span className="text-primary">{mailboxLabel(mailbox)}</span></p>
          <div className="min-h-64 flex-1 overflow-hidden rounded-lg border border-border">
            <RichTextEditor ref={editorRef} className="h-full min-h-64" initialHtml={current?.html_body || ''} resetKey={`${mailbox}:${current?.updated_at || 'new'}`} ariaLabel={`Firma para ${mailboxLabel(mailbox)}`} placeholder="Escribe tu nombre, cargo, teléfono…" onChange={() => { setDirty(true); setSaved(false) }} />
          </div>
          {error && <Alert variant="destructive" className="mt-3">{error}</Alert>}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
            <span aria-live="polite" className="text-xs text-muted-foreground">{saved ? 'Firma guardada.' : dirty ? 'Cambios sin guardar' : ''}</span>
            <div className="flex flex-wrap gap-2">
              {current && <Button type="button" variant="outline" disabled={busy} onClick={() => { if (window.confirm('¿Eliminar esta firma?')) void save(true) }}>Eliminar firma</Button>}
              <Button type="button" disabled={busy || !dirty} onClick={() => void save()}>{busy ? 'Guardando…' : 'Guardar firma'}</Button>
            </div>
          </div>
        </div>
      </div>
    </div>}
  </dialog>
}
