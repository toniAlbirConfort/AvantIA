import { useId } from 'react'

type BrandLogoProps = {
  className?: string
}

export function BrandMark({ className = 'size-10' }: BrandLogoProps) {
  const clipId = `avantia-mark-${useId().replaceAll(':', '')}`

  return <svg
    className={className}
    viewBox="0 0 45 45"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <defs>
      <clipPath id={clipId}><circle cx="22.5" cy="22.5" r="22.5" /></clipPath>
    </defs>
    <image href="/avantio-mark.png" x="-15" y="-23" width="61" height="75" preserveAspectRatio="none" clipPath={`url(#${clipId})`} />
  </svg>
}

export function BrandLogo({ className = '' }: BrandLogoProps) {
  return <span className={`inline-flex min-w-0 items-center gap-3 ${className}`}>
    <BrandMark className="size-10 shrink-0" />
    <span className="min-w-0">
      <strong className="block text-base font-semibold leading-tight tracking-tight">AvantIA</strong>
      <small className="block text-[0.65rem] leading-5 text-current/70">HOSPITALIDAD CONECTADA</small>
    </span>
  </span>
}
