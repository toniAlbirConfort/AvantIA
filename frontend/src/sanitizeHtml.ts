// Mirrors backend/app/html_sanitize.py. Any operator-written HTML (drafts, signatures)
// passes through here before it reaches a live, editable DOM node.
const ALLOWED_TAGS = new Set(['A', 'B', 'BLOCKQUOTE', 'BR', 'DIV', 'EM', 'H1', 'H2', 'H3', 'HR', 'I', 'LI', 'OL', 'P', 'S', 'SPAN', 'STRONG', 'SUB', 'SUP', 'TABLE', 'TBODY', 'TD', 'TH', 'THEAD', 'TR', 'U', 'UL', 'IMG', 'FONT'])
const DROP_CONTENT = new Set(['SCRIPT', 'STYLE', 'HEAD', 'TITLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'NOSCRIPT', 'SVG', 'MATH', 'FORM', 'TEXTAREA', 'SELECT', 'BUTTON'])
const ALLOWED_ATTRS: Record<string, string[]> = { A: ['href', 'title'], IMG: ['src', 'alt', 'width', 'height'], TD: ['colspan', 'rowspan'], TH: ['colspan', 'rowspan'], FONT: ['color', 'size'] }
const GLOBAL_ATTRS = ['style', 'data-signature']
const STYLE_PROPERTIES = new Set(['color', 'background-color', 'font-weight', 'font-style', 'text-decoration', 'font-size', 'font-family', 'text-align', 'margin', 'margin-top', 'margin-bottom', 'padding', 'line-height'])
const SAFE_URL = /^(https?:|mailto:|tel:)/i
const SAFE_IMAGE = /^data:image\/(png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i

function cleanStyle(value: string) {
  return value.split(';').map(item => {
    const index = item.indexOf(':')
    if (index < 0) return ''
    const name = item.slice(0, index).trim().toLowerCase()
    const raw = item.slice(index + 1).trim()
    if (!STYLE_PROPERTIES.has(name) || !raw || /url\s*\(|expression|javascript:|[<>\\]/i.test(raw)) return ''
    return `${name}: ${raw}`
  }).filter(Boolean).join('; ')
}

function cleanNode(source: Node, target: Node, doc: Document) {
  source.childNodes.forEach(child => {
    if (child.nodeType === Node.TEXT_NODE) { target.appendChild(doc.createTextNode(child.textContent || '')); return }
    if (child.nodeType !== Node.ELEMENT_NODE) return
    const element = child as Element
    const tag = element.tagName.toUpperCase()
    if (DROP_CONTENT.has(tag)) return
    if (!ALLOWED_TAGS.has(tag)) { cleanNode(element, target, doc); return }
    const clean = doc.createElement(tag.toLowerCase())
    for (const name of [...(ALLOWED_ATTRS[tag] || []), ...GLOBAL_ATTRS]) {
      let value = element.getAttribute(name)
      if (value === null) continue
      value = value.trim()
      if (name === 'href' && !SAFE_URL.test(value)) continue
      if (name === 'src' && !SAFE_IMAGE.test(value)) continue
      if (name === 'style') { value = cleanStyle(value); if (!value) continue }
      clean.setAttribute(name, value)
    }
    if (tag === 'IMG' && !clean.hasAttribute('src')) return
    if (tag === 'A') { clean.setAttribute('rel', 'noopener noreferrer'); clean.setAttribute('target', '_blank') }
    cleanNode(element, clean, doc)
    target.appendChild(clean)
  })
}

export function sanitizeHtml(source: string) {
  const parsed = new DOMParser().parseFromString(`<!doctype html><body>${source || ''}`, 'text/html')
  const out = document.implementation.createHTMLDocument('')
  const root = out.createElement('div')
  cleanNode(parsed.body, root, out)
  return root.innerHTML.trim()
}

export function escapeHtml(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

export function textToHtml(text: string) {
  return (text || '').trim().split(/\n{2,}/).filter(item => item.trim())
    .map(item => `<p>${escapeHtml(item).replace(/\n/g, '<br>')}</p>`).join('<p><br></p>')
}

/** Plain-text version of composer HTML, used as the text/plain part of the email. */
export function htmlToText(html: string) {
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html}`, 'text/html')
  doc.querySelectorAll('br').forEach(node => node.replaceWith('\n'))
  doc.querySelectorAll('li').forEach(node => node.prepend('• '))
  doc.querySelectorAll('p, div, li, tr, h1, h2, h3, blockquote').forEach(node => node.append('\n'))
  doc.querySelectorAll('a[href]').forEach(node => {
    const href = node.getAttribute('href') || ''
    if (href && href !== node.textContent && !href.startsWith('mailto:')) node.append(` (${href})`)
  })
  return (doc.body.textContent || '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}
