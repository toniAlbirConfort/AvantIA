type BrandLogoProps = {
  className?: string
}

export function BrandMark({ className = 'size-10' }: BrandLogoProps) {
  return <img
    src="/brand-mark.svg"
    className={className}
    alt=""
    aria-hidden="true"
    draggable={false}
  />
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
