import type { InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Input({ className, type, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input data-slot="input" type={type} className={cn('h-10 w-full rounded-md border border-[var(--line)] bg-white px-3 py-2 text-sm shadow-sm outline-none transition-[border-color,box-shadow] placeholder:text-[var(--muted)] focus-visible:border-[var(--brand)] focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-50', className)} {...props} />
}
