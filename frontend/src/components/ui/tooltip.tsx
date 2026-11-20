import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

const WIDTH = 288 // w-72
const GAP = 6
const MARGIN = 16

/**
 * Small explanation attached to an icon button. Opens on hover, keyboard focus and
 * tap (touch screens have no hover), closes with Escape or when focus leaves.
 *
 * The panel is positioned against the viewport so scrolling containers (wide tables)
 * do not clip it, and it opens above the button when there is no room below.
 */
export function Tooltip({ label, content, children, className }: { label: string; content: ReactNode; children: ReactNode; className?: string }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' })
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLSpanElement>(null)

  const place = useCallback(() => {
    const button = trigger.current?.getBoundingClientRect()
    if (!button) return
    const width = Math.min(WIDTH, window.innerWidth - MARGIN * 2)
    const left = Math.max(MARGIN, Math.min(button.right - width, window.innerWidth - width - MARGIN))
    const height = panel.current?.offsetHeight ?? 0
    const below = button.bottom + GAP
    const top = below + height > window.innerHeight - MARGIN && button.top - GAP - height > MARGIN
      ? button.top - GAP - height
      : below
    setStyle({ left, top, width })
  }, [])

  useEffect(() => {
    if (!open) {
      setStyle({ visibility: 'hidden' })
      return
    }
    place()
    // Measure again once the panel has its real height.
    const frame = requestAnimationFrame(place)
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  return <span data-slot="tooltip" className="inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
    <button
      ref={trigger}
      type="button"
      aria-label={label}
      aria-describedby={open ? id : undefined}
      aria-expanded={open}
      className={cn('inline-flex size-8 items-center justify-center rounded-md outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring', className)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={() => setOpen(current => !current)}
      onKeyDown={event => { if (event.key === 'Escape') setOpen(false) }}
    >{children}</button>
    {open && <span ref={panel} role="tooltip" id={id} style={style} className="fixed z-50 grid gap-1.5 whitespace-normal break-words rounded-md border border-border bg-popover p-3 text-left text-xs font-normal leading-5 text-popover-foreground shadow-md">{content}</span>}
  </span>
}
