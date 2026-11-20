import { useId, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Small explanation attached to an icon button. Opens on hover, keyboard focus and
 * tap (touch screens have no hover), closes with Escape or when focus leaves.
 */
export function Tooltip({ label, content, children, className }: { label: string; content: ReactNode; children: ReactNode; className?: string }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  return <span data-slot="tooltip" className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
    <button
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
    {open && <span role="tooltip" id={id} className="absolute right-0 top-full z-20 mt-1 grid w-72 max-w-[calc(100vw-2rem)] gap-1.5 rounded-md border border-border bg-popover p-3 text-left text-xs font-normal leading-5 text-popover-foreground shadow-md">{content}</span>}
  </span>
}
