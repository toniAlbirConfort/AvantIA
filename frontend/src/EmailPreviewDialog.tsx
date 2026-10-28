import { useEffect, useRef } from 'react'
import type { Message } from './types'
import { Button } from './components/ui/button'
import { X } from 'lucide-react'

export function emailDocument(source: string) {
  const policy = "default-src 'none'; script-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; connect-src 'none'; form-action 'none'; frame-src 'none'; object-src 'none'; media-src 'none'; base-uri 'none'"
  return '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="' + policy + '"><meta name="referrer" content="no-referrer"><style>html,body{margin:0;padding:0;max-width:100%;overflow-wrap:anywhere}body{padding:12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#243a41}img{max-width:100%;height:auto}</style>' + source
}

type Props = {
  message: Message | null
  fallbackSubject?: string | null
  onClose: () => void
}

export function EmailPreviewDialog({ message, fallbackSubject, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const subject = message?.subject || fallbackSubject || '(sin asunto)'

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (message && !dialog.open) dialog.showModal()
    if (!message && dialog.open) dialog.close()
  }, [message])

  return <dialog
    ref={dialogRef}
    aria-labelledby="expanded-email-title"
    onClose={onClose}
    className="m-auto max-h-[94vh] w-[96vw] max-w-6xl overflow-hidden rounded-xl border border-border bg-card p-0 text-foreground shadow-2xl backdrop:bg-background/80"
  >
    {message && <div className="flex max-h-[94vh] flex-col">
      <header className="flex items-start justify-between gap-4 border-b border-border p-4 sm:p-6">
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-primary">VISTA AMPLIADA DEL EMAIL</p>
          <h2 id="expanded-email-title" className="break-words text-lg font-semibold sm:text-xl">{subject}</h2>
          <dl className="mt-3 grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[3rem_minmax(0,1fr)]">
            <dt className="text-muted-foreground">De</dt>
            <dd className="m-0 break-all">{message.sender || '—'}</dd>
            {message.recipient && <>
              <dt className="text-muted-foreground">Para</dt>
              <dd className="m-0 break-all">{message.recipient}</dd>
            </>}
          </dl>
        </div>
        <Button type="button" variant="ghost" size="icon" className="shrink-0" onClick={() => dialogRef.current?.close()} aria-label="Cerrar vista ampliada">
          <X aria-hidden="true" />
        </Button>
      </header>
      <div className="min-h-0 overflow-auto">
        {message.html_body
          ? <iframe className="block h-[75vh] min-h-72 w-full border-0 bg-white" title="Cuerpo del correo" sandbox="" referrerPolicy="no-referrer" srcDoc={emailDocument(message.html_body)} />
          : <div className="min-h-72 whitespace-pre-wrap break-words bg-white p-5 text-sm leading-6 text-slate-900">{message.body || '(correo sin texto)'}</div>}
        {message.source_url && <div className="border-t border-border p-4">
          <a className="text-sm text-primary underline underline-offset-4" href={message.source_url} target="_blank" rel="noreferrer">Abrir mensaje original ↗</a>
        </div>}
      </div>
    </div>}
  </dialog>
}
