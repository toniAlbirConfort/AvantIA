import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span data-slot="badge" className={cn('inline-flex items-center rounded-full border border-transparent px-2.5 py-1 text-xs font-medium', className)} {...props} />
}
