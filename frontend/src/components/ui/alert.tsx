import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type AlertVariant = 'default' | 'destructive' | 'warning' | 'success' | 'info'

const variants: Record<AlertVariant, string> = {
  default: 'border-border bg-card text-card-foreground',
  destructive: 'border-error-ink/30 bg-error-bg text-error-ink',
  warning: 'border-warning-ink/30 bg-warning-bg text-warning-ink',
  success: 'border-success-ink/30 bg-success-bg text-success-ink',
  info: 'border-info-ink/30 bg-info-bg text-info-ink',
}

export function Alert({ className, variant = 'default', ...props }: HTMLAttributes<HTMLDivElement> & { variant?: AlertVariant }) {
  return <div role="alert" data-slot="alert" className={cn('relative w-full rounded-lg border px-4 py-3 text-sm [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-3.5 [&>svg]:size-4 [&>svg~*]:pl-7', variants[variant], className)} {...props} />
}
