import { useLayoutEffect, useRef, useState } from 'react'
import { useLive } from './App.jsx'
import { Card, PageHeader, Segmented, StatusBadge, cx, plural, taka } from './ui.jsx'

const RANGES = [7, 30, 90]
const shortDate = (iso) => new Date(iso + 'T00:00').toLocaleDateString([], { day: 'numeric', month: 'short' })

function Delta({ now, before }) {
  if (!before) return <span className="text-xs text-faint">No earlier data</span>
  const pct = Math.round(((now - before) / before) * 100)
  return (
    <span className={cx('chip px-1.5!', pct >= 0 ? 'bg-ok/10 text-ok' : 'bg-danger/10 text-danger')}>
      {pct >= 0 ? '↑' : '↓'} {Math.abs(pct)}%
    </span>
  )
}

// One cell of the stats strip.
const Stat = ({ label, value, foot, className }) => (
  <div className={cx('bg-surface px-5 py-5', className)}>
    <div className="text-sm text-muted">{label}</div>
    <div className="mt-2 text-[1.65rem] leading-none font-semibold tracking-tight tabular-nums">{value}</div>
    <div className="mt-3 h-5 flex items-center text-xs text-faint">{foot}</div>
  </div>
)

function RevenueChart({ daily }) {
  const [hover, setHover] = useState(null)
  const [W, setW] = useState(800)
  const box = useRef(null)
  useLayoutEffect(() => {
    setW(Math.max(280, box.current.clientWidth))  // measure before first paint so bars don't jump
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [])
  const H = W < 500 ? 200 : 260, L = 40, B = 28, T = 12
  const max = Math.max(...daily.map((d) => d.revenue), 1)
  const step = 10 ** Math.floor(Math.log10(max))
  const top = Math.ceil(max / step) * step
  const ticks = [0, 0.5, 1].map((f) => f * top)
  const slot = (W - L) / daily.length
  const bw = Math.max(2, Math.min(22, slot * 0.62))
  const y = (v) => T + (H - T - B) * (1 - v / top)
  const every = Math.ceil(daily.length / Math.max(2, Math.floor(W / 90)))
  const d = hover != null && daily[hover]
  return (
    <div className="relative" ref={box}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block" role="img" aria-label="Revenue per day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="stroke-line" strokeDasharray={t ? '3 4' : undefined} strokeWidth="1" />
            <text x={L - 10} y={y(t) + 4} textAnchor="end" className="fill-faint text-[11px] tabular-nums">{t >= 1000 ? `${t / 1000}k` : t}</text>
          </g>
        ))}
        {daily.map((p, i) => {
          const x = L + i * slot + (slot - bw) / 2, h = H - B - y(p.revenue), r = Math.min(4, bw / 2, h)
          return (
            <g key={p.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={L + i * slot} y={T} width={slot} height={H - T - B} className={hover === i ? 'fill-subtle' : 'fill-transparent'} />
              {h > 0 && <path className={cx('grow transition-opacity duration-150 fill-brand', hover != null && hover !== i && 'opacity-35')}
                style={{ animationDelay: `${i * 8}ms` }}
                d={`M${x},${H - B} v${-(h - r)} q0,${-r} ${r},${-r} h${bw - 2 * r} q${r},0 ${r},${r} v${h - r} z`} />}
              {i % every === 0 && <text x={L + i * slot + slot / 2} y={H - 8} textAnchor="middle" className="fill-faint text-[11px]">{shortDate(p.date)}</text>}
            </g>
          )
        })}
      </svg>
      {d && (
        <div className="pointer-events-none absolute top-0 -translate-x-1/2 transition-[left] duration-100 rounded-xl bg-ink text-inverse px-3 py-2 text-xs shadow-xl whitespace-nowrap"
          style={{ left: `${((L + hover * slot + slot / 2) / W) * 100}%` }}>
          <div className="opacity-60">{shortDate(d.date)}</div>
          <div className="font-semibold text-sm tabular-nums">{taka(d.revenue)}</div>
          <div className="opacity-60">{plural(d.orders, 'order')}</div>
        </div>
      )}
    </div>
  )
}

function BarList({ rows, sub }) {
  const max = Math.max(...rows.map((r) => r.revenue), 1)
  if (!rows.length) return <p className="text-sm text-faint">No sales in this period.</p>
  return (
    <ul className="space-y-4">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="truncate">{r.label}</span>
            <span className="font-medium tabular-nums">{taka(r.revenue)}</span>
          </div>
          <div className="mt-2 h-1.5 rounded-full bg-subtle overflow-hidden">
            <div className="h-full rounded-full bg-brand transition-[width] duration-500 ease-out" style={{ width: `${(r.revenue / max) * 100}%` }} />
          </div>
          {sub && <div className="mt-1.5 text-xs text-faint">{sub(r)}</div>}
        </li>
      ))}
    </ul>
  )
}

export default function Analytics() {
  const [days, setDays] = useState(30)
  const [fresh] = useLive(`/api/analytics?days=${days}`, 15000)
  const last = useRef(null)
  if (fresh) last.current = fresh
  const data = fresh ?? last.current  // keep the old range on screen (dimmed) while the new one loads
  const loading = !fresh
  const c = data?.current, p = data?.previous
  const delta = (k) => data && <><Delta now={c[k]} before={p[k]} /><span className="ml-2 hidden sm:inline">vs previous {days} days</span></>
  const totalOrders = data?.status.reduce((s, x) => s + x.count, 0)

  return (
    <>
      <PageHeader title="Overview" sub="Sales your assistant made on WhatsApp, Messenger and Instagram.">
        <Segmented label="Date range" value={days} onChange={setDays} options={RANGES.map((r) => ({ value: r, label: `${r} days` }))} />
      </PageHeader>

      <div className={cx('space-y-6 transition-opacity', loading && 'opacity-50')}>
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-px overflow-hidden rounded-2xl border border-line bg-line">
          <Stat label="Revenue" value={c ? taka(c.revenue) : '–'} foot={delta('revenue')} />
          <Stat label="Orders" value={c?.orders ?? '–'} foot={delta('orders')} />
          <Stat label="Average order" value={c ? taka(c.avg_order) : '–'} foot={delta('avg_order')} />
          <Stat label="Handled by AI" value={c ? `${c.ai_handled_pct}%` : '–'}
            foot={c && `${plural(c.chats, 'chat')} · ${c.conversion_pct}% ordered`} />
          <Stat label="AI cost" className="col-span-2 lg:col-span-1" value={c ? `$${c.ai_cost_usd.toFixed(2)}` : '–'}
            foot={c && (c.chats ? `$${(c.ai_cost_usd / c.chats).toFixed(3)} per chat` : `${Math.round(c.ai_tokens / 1000)}k tokens`)} />
        </div>

        <Card title="Revenue per day" action={data && <span className="text-sm text-muted tabular-nums">{taka(c.revenue)} total</span>}>
          {data ? <RevenueChart key={data.days} daily={data.daily} /> : <div className="h-64 rounded-xl bg-subtle animate-pulse" />}
        </Card>

        {data && (
          <div className="grid gap-6 lg:grid-cols-3">
            <Card title="Top products">
              <BarList rows={data.top_products} sub={(r) => `${r.count} sold`} />
            </Card>
            <Card title="How customers pay">
              <BarList rows={data.payment} sub={(r) => plural(r.count, 'order')} />
              <h2 className="text-sm font-medium mt-8 mb-5">Delivery area</h2>
              <BarList rows={data.delivery} sub={(r) => plural(r.count, 'order')} />
            </Card>
            <Card title="Order status" action={<a href="#/orders" className="text-sm text-muted hover:text-ink">View all →</a>}>
              <ul className="space-y-3">
                {data.status.map((s) => (
                  <li key={s.status} className="flex items-center justify-between gap-3">
                    <StatusBadge status={s.status} />
                    <span className="text-sm tabular-nums">
                      {s.count}<span className="text-faint ml-2 inline-block w-9 text-right">{totalOrders ? Math.round((100 * s.count) / totalOrders) : 0}%</span>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        )}
      </div>
    </>
  )
}
