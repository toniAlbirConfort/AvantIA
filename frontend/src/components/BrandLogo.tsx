type BrandLogoProps = {
  className?: string
}

export function BrandMark({ className = 'size-10' }: BrandLogoProps) {
  return <svg
    className={className}
    viewBox="0 0 40 40"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <rect width="40" height="40" rx="12" fill="#F15F41" />
    <path d="M9.5 31V20.5c0-6.4 4.7-11.5 10.5-11.5s10.5 5.1 10.5 11.5V31" stroke="white" strokeWidth="3.25" strokeLinecap="round" />
    <path d="M15.5 31v-7a4.5 4.5 0 0 1 9 0v7" stroke="#00CCCC" strokeWidth="3.25" strokeLinecap="round" />
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
