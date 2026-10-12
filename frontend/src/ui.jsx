// Shared building blocks. Colours come from the tokens in index.css, so everything works in light and dark.
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

export const cx = (...classes) => classes.filter(Boolean).join(' ')
// Amounts are whole cents. The currency symbol comes from business.yaml (App sets it once /api/business loads).
let currency = '$'
export const setCurrency = (symbol) => { currency = symbol }
export const money = (cents) => `${currency}${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
export const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// Lucide-style 24px stroke icons.
const ICONS = {
  overview: 'M3 3v18h18M7 16v-4M12 16V8M17 16V5',
  chats: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  orders: 'M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0',
  products: 'M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82zM7 7h.01',
  simulator: 'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  mic: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
  plus: 'M12 5v14M5 12h14',
  x: 'M18 6 6 18M6 6l12 12',
  back: 'M15 18l-6-6 6-6',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  image: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM21 15l-5-5L5 21',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41',
  moon: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z',
  system: 'M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM8 21h8M12 17v4',
  check: 'M20 6 9 17l-5-5',
  bot: 'M12 8V4H8M4 12a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v4a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM9 13v2M15 13v2',
  kitchen: 'M6 13.87A4 4 0 0 1 7.41 6a5.11 5.11 0 0 1 1.05-1.54 5 5 0 0 1 7.08 0A5.11 5.11 0 0 1 16.59 6 4 4 0 0 1 18 13.87V21H6zM6 17h12',
  bookings: 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18',
  menu: 'M17 8h1a4 4 0 1 1 0 8h-1M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4zM6 2v2M10 2v2M14 2v2',
  bell: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0',
  pause: 'M10 4H6v16h4zM18 4h-4v16h4z',
  approvals: 'M22 11.08V12a10 10 0 1 1-5.93-9.14M22 4 12 14.01l-3-3',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
}
export const Icon = ({ name, className = 'w-[18px] h-[18px]' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d={ICONS[name]} />
  </svg>
)

export const STATUS_STYLE = {
  new: 'bg-info/10 text-info', preparing: 'bg-warn/10 text-warn', ready: 'bg-violet/10 text-violet',
  completed: 'bg-ok/10 text-ok', cancelled: 'bg-danger/10 text-danger',
  booked: 'bg-info/10 text-info', arrived: 'bg-ok/10 text-ok', no_show: 'bg-danger/10 text-danger',
  pending: 'bg-warn/10 text-warn', approved: 'bg-ok/10 text-ok', declined: 'bg-danger/10 text-danger',
}
export const statusLabel = (s) => s[0].toUpperCase() + s.slice(1).replace('_', '-')
export const StatusBadge = ({ status }) => <span className={cx('chip', STATUS_STYLE[status])}>{statusLabel(status)}</span>

export const Card = ({ title, action, className, children }) => (
  <section className={cx('card p-5', className)}>
    {title && (
      <header className="flex items-center justify-between gap-3 mb-5">
        <h2 className="text-sm font-medium text-ink">{title}</h2>
        {action}
      </header>
    )}
    {children}
  </section>
)

export const PageHeader = ({ title, sub, children }) => (
  <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
      {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
    </div>
    {children && <div className="flex items-center gap-2">{children}</div>}
  </div>
)

export const Skeleton = ({ className, style }) => <div style={style} className={cx('rounded-lg bg-subtle animate-pulse', className)} />

export const Empty = ({ icon, title, children }) => (
  <div className="grid place-items-center text-center px-6 py-16">
    {icon && <div className="mb-3 grid place-items-center w-11 h-11 rounded-2xl bg-subtle text-faint"><Icon name={icon} /></div>}
    <div className="text-sm font-medium text-ink">{title}</div>
    {children && <div className="mt-1 text-sm text-muted max-w-xs">{children}</div>}
  </div>
)

// options: [{ value, label, count? }]
export function Segmented({ options, value, onChange, label }) {
  return (
    <div role="group" aria-label={label} className="inline-flex max-w-full overflow-x-auto rounded-xl bg-subtle p-1">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)} aria-pressed={value === o.value}
          className={cx('h-8 px-3 rounded-lg text-sm whitespace-nowrap transition',
            value === o.value ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink')}>
          {o.label}
          {o.count != null && <span className={cx('ml-1.5 tabular-nums', value === o.value ? 'text-muted' : 'text-faint')}>{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

export const Switch = ({ checked, onChange, label }) => (
  <button role="switch" aria-checked={checked} onClick={onChange} className="flex items-center gap-2.5 text-sm text-muted hover:text-ink">
    {label}
    <span className={cx('w-9 h-5 rounded-full p-0.5 transition-colors', checked ? 'bg-brand' : 'bg-line')}>
      <span className={cx('block w-4 h-4 rounded-full bg-white shadow-sm transition-transform', checked && 'translate-x-4')} />
    </span>
  </button>
)

// Side panel over the page. Escape or a click outside closes it.
export function Drawer({ title, sub, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { removeEventListener('keydown', onKey); document.body.style.overflow = overflow }
  }, [onClose])
  return createPortal(
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fade-in absolute inset-0 bg-black/30 backdrop-blur-[2px]" onClick={onClose} />
      <div className="slide-in absolute inset-y-0 right-0 flex w-full max-w-lg flex-col bg-surface border-l border-line shadow-2xl">
        <header className="flex items-start justify-between gap-4 px-6 py-5 border-b border-line">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink truncate">{title}</h2>
            {sub && <p className="mt-0.5 text-sm text-muted">{sub}</p>}
          </div>
          <button onClick={onClose} className="btn btn-ghost h-8! px-2! -mr-2" aria-label="Close"><Icon name="x" /></button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-6">{children}</div>
      </div>
    </div>,
    document.body,  // outside the page's animated wrapper, so the panel covers the whole screen
  )
}
