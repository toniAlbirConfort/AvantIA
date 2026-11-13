import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { ClipboardEvent, DragEvent, KeyboardEvent, MouseEvent, ReactNode } from 'react'
import { Bold, Check, ImagePlus, Italic, Link2, List, ListOrdered, Redo2, RemoveFormatting, Strikethrough, Underline, Undo2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { escapeHtml, sanitizeHtml } from './sanitizeHtml'

const MAX_IMAGE_CHARS = 1_500_000
const MAX_IMAGE_PIXELS_WIDE = 1200
const IMAGE_SIZES: [string, number | null][] = [['Pequeña', 120], ['Mediana', 240], ['Grande', 400], ['Original', null]]

function readAsDataUrl(file: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('No se pudo leer la imagen.'))
    reader.readAsDataURL(file)
  })
}

/** Image file -> embeddable data URL, downscaled so signatures and replies stay light. */
async function imageFileToDataUrl(file: File): Promise<{ src: string; width: number }> {
  if (!/^image\/(png|jpe?g|gif|webp)$/.test(file.type)) throw new Error('Formato no admitido. Usa una imagen PNG, JPG, GIF o WebP.')
  const original = await readAsDataUrl(file)
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const element = new Image()
    element.onload = () => resolve(element)
    element.onerror = () => reject(new Error('El archivo no es una imagen válida.'))
    element.src = original
  })
  const scale = Math.min(1, MAX_IMAGE_PIXELS_WIDE / image.naturalWidth)
  // GIFs keep their animation; small images are embedded untouched.
  if (file.type === 'image/gif' || (scale === 1 && original.length <= 300_000)) {
    if (original.length > MAX_IMAGE_CHARS) throw new Error('La imagen es demasiado grande (máximo 1 MB).')
    return { src: original, width: image.naturalWidth }
  }
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(image.naturalWidth * scale)
  canvas.height = Math.round(image.naturalHeight * scale)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('No se pudo procesar la imagen.')
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  let src = canvas.toDataURL(file.type === 'image/jpeg' ? 'image/jpeg' : 'image/png', 0.9)
  if (src.length > MAX_IMAGE_CHARS) {
    const flat = document.createElement('canvas')
    flat.width = canvas.width
    flat.height = canvas.height
    const flatContext = flat.getContext('2d')!
    flatContext.fillStyle = '#ffffff'
    flatContext.fillRect(0, 0, flat.width, flat.height)
    flatContext.drawImage(canvas, 0, 0)
    src = flat.toDataURL('image/jpeg', 0.85)
  }
  if (src.length > MAX_IMAGE_CHARS) throw new Error('La imagen es demasiado grande (máximo 1 MB).')
  return { src, width: canvas.width }
}

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
  const [imageError, setImageError] = useState('')
  const [selectedImage, setSelectedImage] = useState<HTMLImageElement | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Re-renders the size bar after a resize, which changes the DOM but no React state.
  const [, setImageVersion] = useState(0)

  const updateEmpty = () => setEmpty(!(editorRef.current?.textContent || '').trim() && !editorRef.current?.querySelector('img'))

  useEffect(() => {
    if (!editorRef.current) return
    editorRef.current.innerHTML = sanitizeHtml(initialHtml)
    setSelectedImage(null)
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

  function rememberSelection() {
    const selection = window.getSelection()
    savedRange.current = selection && selection.rangeCount && editorRef.current?.contains(selection.anchorNode) ? selection.getRangeAt(0).cloneRange() : null
  }

  async function insertImages(files: File[]) {
    setImageError('')
    for (const file of files) {
      try {
        const { src, width } = await imageFileToDataUrl(file)
        editorRef.current?.focus()
        const selection = window.getSelection()
        if (savedRange.current && selection) { selection.removeAllRanges(); selection.addRange(savedRange.current) }
        document.execCommand('insertHTML', false, `<img src="${src}" alt="" width="${Math.min(width, 400)}">`)
        rememberSelection()
        changed()
      } catch (err) { setImageError((err as Error).message) }
    }
  }

  function resizeImage(width: number | null) {
    if (!selectedImage) return
    if (width) selectedImage.setAttribute('width', String(width))
    else selectedImage.removeAttribute('width')
    selectedImage.removeAttribute('height')
    setImageVersion(version => version + 1)
    changed()
  }

  function removeImage() {
    selectedImage?.remove()
    setSelectedImage(null)
    changed()
  }

  function clickEditor(event: MouseEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement
    setSelectedImage(target.tagName === 'IMG' && !disabled ? target as HTMLImageElement : null)
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    const files = [...event.dataTransfer.files].filter(file => file.type.startsWith('image/'))
    if (!files.length) return
    event.preventDefault()
    const caret = document.caretRangeFromPoint?.(event.clientX, event.clientY)
    if (caret && editorRef.current?.contains(caret.startContainer)) savedRange.current = caret
    void insertImages(files)
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
    const images = [...event.clipboardData.files].filter(file => file.type.startsWith('image/'))
    if (images.length && !html) { rememberSelection(); void insertImages(images); return }
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
      <ToolButton label="Insertar imagen" disabled={disabled} onPress={() => { rememberSelection(); fileRef.current?.click() }}><ImagePlus /></ToolButton>
      <ToolButton label="Quitar formato" disabled={disabled} onPress={() => { run('removeFormat'); run('unlink') }}><RemoveFormatting /></ToolButton>
      <Divider />
      <ToolButton label="Deshacer (Ctrl+Z)" disabled={disabled} onPress={() => run('undo')}><Undo2 /></ToolButton>
      <ToolButton label="Rehacer (Ctrl+Y)" disabled={disabled} onPress={() => run('redo')}><Redo2 /></ToolButton>
      {toolbarEnd && <div className="ml-auto flex items-center gap-2">{toolbarEnd}</div>}
    </div>
    <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden onChange={event => { const files = [...(event.target.files || [])]; event.target.value = ''; void insertImages(files) }} />
    {selectedImage && editorRef.current?.contains(selectedImage) && <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-3 py-1.5 text-xs">
      <span className="mr-1 font-medium text-muted-foreground">Tamaño de la imagen</span>
      {IMAGE_SIZES.map(([name, width]) => <button key={name} type="button" onMouseDown={event => { event.preventDefault(); resizeImage(width) }} className={`rounded-md border px-2 py-1 hover:bg-accent ${selectedImage.getAttribute('width') === (width ? String(width) : null) ? 'border-primary bg-accent font-semibold' : 'border-border'}`}>{name}</button>)}
      <button type="button" onMouseDown={event => { event.preventDefault(); removeImage() }} className="ml-auto rounded-md border border-border px-2 py-1 text-error-ink hover:bg-error-bg">Quitar imagen</button>
    </div>}
    {imageError && <p role="alert" className="m-0 flex items-center justify-between gap-2 border-b border-border bg-error-bg px-3 py-1.5 text-xs text-error-ink">{imageError}<button type="button" aria-label="Cerrar aviso" onClick={() => setImageError('')}><X className="size-3.5" /></button></p>}
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
        onDrop={drop}
        onClick={clickEditor}
        onKeyDown={keyDown}
      />
    </div>
  </div>
})
