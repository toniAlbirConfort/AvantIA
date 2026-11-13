import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, MoreHorizontal } from 'lucide-react'
import { emailDocument } from './EmailPreviewDialog'
import type { Message } from './types'

// Common markers mail clients use to start the quoted history of previous messages.
const QUOTE_SELECTORS = ['.gmail_quote', 'blockquote[type="cite"]', '#divRplyFwdMsg', '#appendonsend', '.yahoo_quoted', '#mail-editor-reference-message-container', '.moz-cite-prefix']
const HIDE_QUOTES_CSS = `${QUOTE_SELECTORS.join(',')},#divRplyFwdMsg ~ *,#appendonsend ~ *,.moz-cite-prefix ~ *{display:none!important}`
const PLAIN_QUOTE_START = /^(?:-{2,}\s*(?:original message|mensaje original)\s*-{2,}|(?:on|el) .{4,200}(?:wrote|escribió):)/im
// Only replies repeat earlier messages; forwards (leads sent on by colleagues) must stay whole.
const REPLY_SUBJECT = /^\s*(?:re|aw|sv|antw|r)\s*:/i

export function splitAddress(value?: string | null) {
  const raw = (value || '').trim()
  const match = raw.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/)
  if (match) return { name: match[1].trim() || match[2], address: match[2] }
  return { name: raw || 'Desconocido', address: raw }
}

function initials(name: string) {
  const parts = name.replace(/[^\p{L}\s]/gu, ' ').trim().split(/\s+/).filter(Boolean)
  return ((parts[0]?.[0] || '?') + (parts[1]?.[0] || '')).toUpperCase()
}

const longDate = (value: string) => new Intl.DateTimeFormat('es-ES', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))

function HtmlBody({ html, title, collapsible, showQuoted, onHasQuoted }: { html: string; title: string; collapsible: boolean; showQuoted: boolean; onHasQuoted: (value: boolean) => void }) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(160)

  const measure = () => {
    const body = frameRef.current?.contentDocument?.body
    if (!body) return
    // The body, not <html>: the root never reports less than the iframe's current height.
    setHeight(Math.min(Math.max(Math.ceil(body.getBoundingClientRect().height), body.scrollHeight, 48) + 4, 6000))
  }
  const applyQuoteVisibility = () => {
    const doc = frameRef.current?.contentDocument
    if (!doc?.head) return
    const hasQuoted = collapsible && QUOTE_SELECTORS.some(selector => doc.querySelector(selector))
    onHasQuoted(hasQuoted)
    let style = doc.getElementById('avantia-quotes')
    if (!style) { style = doc.createElement('style'); style.id = 'avantia-quotes'; doc.head.append(style) }
    style.textContent = showQuoted || !hasQuoted ? '' : HIDE_QUOTES_CSS
    measure()
  }

  useEffect(() => { applyQuoteVisibility() }, [showQuoted]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const frame = frameRef.current
    if (!frame || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => measure())
    observer.observe(frame)
    return () => observer.disconnect()
  }, [])

  // allow-same-origin (without allow-scripts) only lets the parent measure the content;
  // the email itself still cannot run scripts or load remote resources (see emailDocument CSP).
  return <iframe
    ref={frameRef}
    title={title}
    sandbox="allow-same-origin"
    referrerPolicy="no-referrer"
    srcDoc={emailDocument(html)}
    onLoad={() => { applyQuoteVisibility(); window.setTimeout(measure, 250) }}
    style={{ height }}
    className="block w-full border-0 bg-white"
  />
}

function ThreadMessage({ message, expanded, onToggle, fallbackSender, fallbackSubject }: {
  message: Message; expanded: boolean; onToggle: () => void; fallbackSender?: string | null; fallbackSubject?: string | null
}) {
  const [showQuoted, setShowQuoted] = useState(false)
  const [hasQuoted, setHasQuoted] = useState(false)
  const outbound = message.direction === 'outbound'
  const from = splitAddress(message.sender || (outbound ? 'AvantIA' : fallbackSender))
  const to = message.recipient ? splitAddress(message.recipient) : null
  const plain = message.body || ''
  const collapsible = REPLY_SUBJECT.test(message.subject || fallbackSubject || '')
  const quoteIndex = message.html_body || !collapsible ? -1 : plain.search(PLAIN_QUOTE_START)
  const plainQuoted = quoteIndex > 0
  const preview = plain.replace(/\s+/g, ' ').trim().slice(0, 160)

  return <article className={`overflow-hidden rounded-lg border bg-card ${expanded ? 'border-border shadow-sm' : 'border-transparent hover:border-border'}`}>
    <button type="button" aria-expanded={expanded} onClick={onToggle} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
      <span aria-hidden="true" className={`grid size-9 shrink-0 place-items-center rounded-full text-xs font-semibold ${outbound ? 'bg-brand-orange text-white' : 'bg-brand-blue-light text-brand-blue-ink'}`}>{initials(from.name)}</span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline justify-between gap-x-3">
          <strong className="truncate text-sm">{from.name}{outbound && <span className="ml-2 rounded bg-accent px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">Enviado</span>}</strong>
          <span className="shrink-0 text-xs text-muted-foreground">{longDate(message.created_at)}</span>
        </span>
        {expanded
          ? <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
              {from.address !== from.name && <span className="block truncate">{from.address}</span>}
              {to && <span className="block truncate">Para: {to.name !== to.address ? `${to.name} <${to.address}>` : to.address}</span>}
            </span>
          : <span className="mt-0.5 block truncate text-sm text-muted-foreground">{preview || '(sin texto)'}</span>}
      </span>
      {expanded ? <ChevronUp aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground" /> : <ChevronDown aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground" />}
    </button>
    {expanded && <div className="border-t border-border">
      {(message.subject || fallbackSubject) && <p className="m-0 border-b border-border px-4 py-2 text-sm font-medium">{message.subject || fallbackSubject}</p>}
      {message.html_body
        ? <HtmlBody html={message.html_body} title={`Correo de ${from.name}`} collapsible={collapsible} showQuoted={showQuoted} onHasQuoted={setHasQuoted} />
        : <div className="whitespace-pre-wrap break-words bg-white px-4 py-3 text-sm leading-6 text-slate-900">{plainQuoted && !showQuoted ? plain.slice(0, quoteIndex).trimEnd() : plain || '(correo sin texto)'}</div>}
      {(hasQuoted || plainQuoted) && <div className="bg-white px-4 pb-3">
        <button type="button" onClick={() => setShowQuoted(value => !value)} className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent" title={showQuoted ? 'Ocultar el historial citado' : 'Mostrar el historial citado'}>
          <MoreHorizontal aria-hidden="true" className="size-4" />{showQuoted ? 'Ocultar historial citado' : 'Mostrar historial citado'}
        </button>
      </div>}
      {message.source_url && <div className="border-t border-border px-4 py-2"><a className="text-xs text-primary underline underline-offset-4" href={message.source_url} target="_blank" rel="noreferrer">Abrir mensaje original ↗</a></div>}
    </div>}
  </article>
}

export function EmailThread({ messages, fallbackSender, fallbackSubject }: { messages: Message[]; fallbackSender?: string | null; fallbackSubject?: string | null }) {
  const ordered = [...messages].reverse()
  const latestId = ordered[0]?.id
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set(latestId ? [latestId] : []))
  const allExpanded = ordered.length > 0 && ordered.every(item => expanded.has(item.id))

  useEffect(() => { if (latestId) setExpanded(current => current.has(latestId) ? current : new Set([...current, latestId])) }, [latestId])

  const toggle = (id: number) => setExpanded(current => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  return <div className="grid gap-2">
    <div className="flex items-center justify-between gap-3 px-1">
      <p className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{messages.length} {messages.length === 1 ? 'mensaje' : 'mensajes'} · más reciente primero</p>
      {ordered.length > 1 && <button type="button" className="text-xs font-medium text-primary underline-offset-4 hover:underline" onClick={() => setExpanded(allExpanded ? new Set(latestId ? [latestId] : []) : new Set(ordered.map(item => item.id)))}>{allExpanded ? 'Contraer todo' : 'Expandir todo'}</button>}
    </div>
    {ordered.map(message => <ThreadMessage key={message.id} message={message} expanded={expanded.has(message.id)} onToggle={() => toggle(message.id)} fallbackSender={fallbackSender} fallbackSubject={fallbackSubject} />)}
  </div>
}
