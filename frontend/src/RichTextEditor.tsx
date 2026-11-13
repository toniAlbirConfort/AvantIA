import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { ClipboardEvent, KeyboardEvent, ReactNode } from 'react'
import { Bold, Check, Italic, Link2, List, ListOrdered, Redo2, RemoveFormatting, Strikethrough, Underline, Undo2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { escapeHtml, sanitizeHtml } from './sanitizeHtml'

export type RichTextEditorHandle = {
  getHtml: () => string
  setSignature: (html: string | null) => void
  focus: () => void
}

type Props = {
  /** Applied when the editor mounts and whenever `resetKey` changes. */
  initialHtml: string
  resetKey?: string | number
  ariaLabel: string
  placeholder?: string
  disabled?: boolean
  className?: string
  editorClassName?: string
  toolbarEnd?: ReactNode
  onChange?: () => void
}

function ToolButton({ label, onPress, children, disabled }: { label: string; onPress: () => void; children: ReactNode; disabled?: boolean }) {
  return <button
    type="button"
    title={label}
    aria-label={label}
    disabled={disabled}
    className="grid size-8 place-items-center rounded-md text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 [&_svg]:size-4"
    onMouseDown={event => { event.preventDefault(); onPress() }}
  >{children}</button>
}

const Divider = () => <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />

export const RichTextEditor = forwardRef<RichTextEditorHandle, Props>(function RichTextEditor(
  { initialHtml, resetKey, ariaLabel, placeholder, disabled, className, editorClassName, toolbarEnd, onChange }, ref,
) {
  const editorRef = useRef<HTMLDivElement>(null)
  const savedRange = useRef<Range | null>(null)
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkUrl, setLinkUrl] = useState('')
  const [empty, setEmpty] = useState(true)

  const updateEmpty = () => setEmpty(!(editorRef.current?.textContent || '').trim() && !editorRef.current?.querySelector('img'))

  useEffect(() => {
    if (!editorRef.current) return
    editorRef.current.innerHTML = sanitizeHtml(initialHtml)
    updateEmpty()
  }, [resetKey])

  useImperativeHandle(ref, () => ({
    getHtml: () => sanitizeHtml(editorRef.current?.innerHTML || ''),
    focus: () => editorRef.current?.focus(),
    setSignature: (html: string | null) => {
      const editor = editorRef.current
      if (!editor) return
      const current = editor.querySelector('[data-signature]')
      if (!html) { current?.remove(); updateEmpty(); return }
      const clean = sanitizeHtml(html)
      if (current) { current.innerHTML = clean; return }
      const block = document.createElement('div')
      block.setAttribute('data-signature', '1')
      block.innerHTML = clean
      if (!editor.lastElementChild || (editor.lastElementChild.textContent || '').trim()) editor.append(Object.assign(document.createElement('p'), { innerHTML: '<br>' }))
      editor.append(block)
      updateEmpty()
    },
  }), [])

  const changed = () => { updateEmpty(); onChange?.() }
  const run = (command: string, value?: string) => {
    editorRef.current?.focus()
    document.execCommand(command, false, value)
    changed()
  }

  function openLink() {
    const selection = window.getSelection()
    savedRange.current = selection && selection.rangeCount && editorRef.current?.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null
    setLinkUrl('https://')
    setLinkOpen(true)
  }

  function applyLink() {
    const raw = linkUrl.trim()
    const url = /^[^:\s]+@[^:\s]+$/.test(raw) ? `mailto:${raw}` : raw
    setLinkOpen(false)
    if (!/^(https?:\/\/|mailto:|tel:)\S+/i.test(url) || url === 'https://') return
    editorRef.current?.focus()
    const selection = window.getSelection()
    if (savedRange.current && selection) { selection.removeAllRanges(); selection.addRange(savedRange.current) }
    if (selection && !selection.isCollapsed) run('createLink', url)
    else run('insertHTML', `<a href="${escapeHtml(url)}">${escapeHtml(raw)}</a>`)
  }

  function paste(event: ClipboardEvent<HTMLDivElement>) {
    const html = event.clipboardData.getData('text/html')
    const text = event.clipboardData.getData('text/plain')
    event.preventDefault()
    if (html) document.execCommand('insertHTML', false, sanitizeHtml(html))
    else document.execCommand('insertText', false, text)
    changed()
  }

  function keyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!(event.ctrlKey || event.metaKey)) return
    const key = event.key.toLowerCase()
    if (key === 'k') { event.preventDefault(); openLink() }
  }

  return <div className={cn('flex min-h-0 flex-col', className)}>
    <div role="toolbar" aria-label="Formato del texto" className="flex flex-wrap items-center gap-0.5 border-b border-border px-2 py-1.5">
      <ToolButton label="Negrita (Ctrl+B)" disabled={disabled} onPress={() => run('bold')}><Bold /></ToolButton>
      <ToolButton label="Cursiva (Ctrl+I)" disabled={disabled} onPress={() => run('italic')}><Italic /></ToolButton>
      <ToolButton label="Subrayado (Ctrl+U)" disabled={disabled} onPress={() => run('underline')}><Underline /></ToolButton>
      <ToolButton label="Tachado" disabled={disabled} onPress={() => run('strikeThrough')}><Strikethrough /></ToolButton>
      <Divider />
      <ToolButton label="Lista con viñetas" disabled={disabled} onPress={() => run('insertUnorderedList')}><List /></ToolButton>
      <ToolButton label="Lista numerada" disabled={disabled} onPress={() => run('insertOrderedList')}><ListOrdered /></ToolButton>
      <ToolButton label="Insertar enlace (Ctrl+K)" disabled={disabled} onPress={openLink}><Link2 /></ToolButton>
      <ToolButton label="Quitar formato" disabled={disabled} onPress={() => { run('removeFormat'); run('unlink') }}><RemoveFormatting /></ToolButton>
      <Divider />
      <ToolButton label="Deshacer (Ctrl+Z)" disabled={disabled} onPress={() => run('undo')}><Undo2 /></ToolButton>
      <ToolButton label="Rehacer (Ctrl+Y)" disabled={disabled} onPress={() => run('redo')}><Redo2 /></ToolButton>
      {toolbarEnd && <div className="ml-auto flex items-center gap-2">{toolbarEnd}</div>}
    </div>
    {linkOpen && <div className="flex items-center gap-2 border-b border-border bg-background px-3 py-2">
      <label className="flex min-w-0 flex-1 items-center gap-2 text-xs font-medium">Enlace
        <input
          autoFocus
          className="h-8 min-w-0 flex-1 rounded-md border border-input bg-card px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={linkUrl}
          onChange={event => setLinkUrl(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); applyLink() } if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setLinkOpen(false) } }}
          placeholder="https://… o correo@dominio.com"
        />
      </label>
      <ToolButton label="Insertar enlace" onPress={applyLink}><Check /></ToolButton>
      <ToolButton label="Cancelar" onPress={() => setLinkOpen(false)}><X /></ToolButton>
    </div>}
    <div className="relative min-h-0 flex-1 overflow-auto bg-white">
      {empty && placeholder && <p aria-hidden="true" className="pointer-events-none absolute left-5 top-4 m-0 text-sm text-slate-400">{placeholder}</p>}
      <div
        ref={editorRef}
        role="textbox"
        aria-multiline="true"
        aria-label={ariaLabel}
        contentEditable={!disabled}
        suppressContentEditableWarning
        spellCheck
        lang="es"
        className={cn('rich-editor min-h-full px-5 py-4 text-[15px] leading-relaxed text-slate-900 outline-none', disabled && 'cursor-not-allowed opacity-70', editorClassName)}
        onInput={changed}
        onPaste={paste}
        onKeyDown={keyDown}
      />
    </div>
  </div>
})
