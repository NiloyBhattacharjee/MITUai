import { useLayoutEffect, useRef, useState } from 'react'
import { useLive } from './App.jsx'

const RANGES = [7, 30, 90]
export const taka = (n) => `৳${Math.round(n).toLocaleString('en-IN')}`
const shortDate = (iso) => new Date(iso + 'T00:00').toLocaleDateString([], { day: 'numeric', month: 'short' })
export const STATUS_STYLE = {
  new: 'bg-sky-50 text-sky-700 ring-sky-600/20', confirmed: 'bg-violet-50 text-violet-700 ring-violet-600/20',
  shipped: 'bg-amber-50 text-amber-800 ring-amber-600/20', delivered: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  cancelled: 'bg-red-50 text-red-700 ring-red-600/20',
}
export const StatusBadge = ({ status }) => (
  <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${STATUS_STYLE[status]}`}>{status}</span>
)
export const Card = ({ title, action, className = '', children }) => (
  <section className={`bg-white rounded-2xl ring-1 ring-zinc-950/5 shadow-sm p-5 ${className}`}>
    {title && <div className="flex items-center justify-between mb-4"><h2 className="text-sm font-semibold text-zinc-900">{title}</h2>{action}</div>}
    {children}
  </section>
)

function Delta({ now, before, days }) {
  if (!before) return <span className="text-xs text-zinc-400">no data for previous {days} days</span>
  const pct = Math.round(((now - before) / before) * 100)
  const up = pct >= 0
  return (
    <span className="text-xs text-zinc-500">
      <span className={`font-medium ${up ? 'text-emerald-700' : 'text-red-600'}`}>{up ? '▲' : '▼'} {Math.abs(pct)}%</span> vs previous {days} days
    </span>
  )
}

function Kpi({ label, value, delta, hint }) {
  return (
    <Card>
      <div className="text-sm text-zinc-500">{label}</div>
      <div className="mt-2 text-3xl font-semibold tracking-tight text-zinc-900">{value}</div>
      <div className="mt-2">{delta ?? <span className="text-xs text-zinc-400">{hint}</span>}</div>
    </Card>
  )
}

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
  const H = W < 500 ? 200 : 260, L = 44, B = 28, T = 12
  const max = Math.max(...daily.map((d) => d.revenue), 1)
  const step = 10 ** Math.floor(Math.log10(max))
  const top = Math.ceil(max / step) * step
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * top)
  const slot = (W - L) / daily.length
  const bw = Math.max(2, Math.min(24, slot * 0.7))
  const y = (v) => T + (H - T - B) * (1 - v / top)
  const every = Math.ceil(daily.length / Math.max(2, Math.floor(W / 90)))
  const d = hover != null && daily[hover]
  return (
    <div className="relative" ref={box}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block" role="img" aria-label="Revenue per day">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W} y1={y(t)} y2={y(t)} stroke={t ? '#e4e4e7' : '#d4d4d8'} strokeWidth="1" />
            <text x={L - 10} y={y(t) + 4} textAnchor="end" className="fill-zinc-400 text-[11px] tabular-nums">
              {t >= 1000 ? `${t / 1000}k` : t}
            </text>
          </g>
        ))}
        {daily.map((p, i) => {
          const x = L + i * slot + (slot - bw) / 2, h = H - B - y(p.revenue), r = Math.min(4, bw / 2, h)
          return (
            <g key={p.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={L + i * slot} y={T} width={slot} height={H - T - B} fill={hover === i ? '#f4f4f5' : 'transparent'} />
              {h > 0 && <path className="grow transition-[fill] duration-150" style={{ animationDelay: `${i * 8}ms` }} fill={hover == null || hover === i ? '#059669' : '#a7f3d0'}
                d={`M${x},${H - B} v${-(h - r)} q0,${-r} ${r},${-r} h${bw - 2 * r} q${r},0 ${r},${r} v${h - r} z`} />}
              {i % every === 0 && <text x={L + i * slot + slot / 2} y={H - 8} textAnchor="middle" className="fill-zinc-400 text-[11px]">{shortDate(p.date)}</text>}
            </g>
          )
        })}
      </svg>
      {d && (
        <div className="pointer-events-none absolute top-0 -translate-x-1/2 transition-[left] duration-100 rounded-lg bg-zinc-900 text-white px-3 py-2 text-xs shadow-lg whitespace-nowrap"
          style={{ left: `${((L + hover * slot + slot / 2) / W) * 100}%` }}>
          <div className="text-zinc-400">{shortDate(d.date)}</div>
          <div className="font-semibold text-sm">{taka(d.revenue)}</div>
          <div className="text-zinc-400">{d.orders} order{d.orders === 1 ? '' : 's'}</div>
        </div>
      )}
    </div>
  )
}

function BarList({ rows, value = (r) => r.revenue, fmt = taka, sub }) {
  const max = Math.max(...rows.map(value), 1)
  if (!rows.length) return <p className="text-sm text-zinc-400">No sales in this period.</p>
  return (
    <ul className="space-y-3.5">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="flex justify-between gap-3 text-sm">
            <span className="text-zinc-700 truncate">{r.label}</span>
            <span className="font-medium text-zinc-900 tabular-nums">{fmt(value(r))}</span>
          </div>
          <div className="mt-1.5 h-1.5 rounded-full bg-zinc-100">
            <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500 ease-out" style={{ width: `${(value(r) / max) * 100}%` }} />
          </div>
          {sub && <div className="mt-1 text-xs text-zinc-400">{sub(r)}</div>}
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
  const delta = (k) => data && <Delta now={c[k]} before={p[k]} days={data.days} />
  const totalOrders = data?.status.reduce((s, x) => s + x.count, 0)

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">Sales overview</h1>
          <p className="text-sm text-zinc-500 mt-1">Orders taken by your AI assistant on WhatsApp, Messenger and Instagram.</p>
        </div>
        <div className="inline-flex rounded-xl bg-white p-1 ring-1 ring-zinc-950/5 shadow-sm self-start" role="group" aria-label="Date range">
          {RANGES.map((r) => (
            <button key={r} onClick={() => setDays(r)} aria-pressed={days === r}
              className={`px-3.5 py-1.5 text-sm rounded-lg transition ${days === r ? 'bg-zinc-900 text-white shadow' : 'text-zinc-600 hover:text-zinc-900'}`}>
              {r} days
            </button>
          ))}
        </div>
      </div>

      <div className={`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4 transition-opacity ${loading ? 'opacity-50' : ''}`}>
        <Kpi label="Revenue" value={c ? taka(c.revenue) : '–'} delta={delta('revenue')} />
        <Kpi label="Orders" value={c?.orders ?? '–'} delta={delta('orders')} />
        <Kpi label="Average order" value={c ? taka(c.avg_order) : '–'} delta={delta('avg_order')} />
        <Kpi label="Handled fully by AI" value={c ? `${c.ai_handled_pct}%` : '–'}
          hint={c && `${c.chats} chat${c.chats === 1 ? '' : 's'} · ${c.conversion_pct}% ordered`} />
        <Kpi label="AI cost" value={c ? `$${c.ai_cost_usd.toFixed(2)}` : '–'}
          hint={c && `${Math.round(c.ai_tokens / 1000)}k tokens${c.chats ? ` · $${(c.ai_cost_usd / c.chats).toFixed(3)} per chat` : ''}`} />
      </div>

      <div className={`space-y-6 transition-opacity ${loading ? 'opacity-50' : ''}`}>
      <Card title="Revenue per day" action={data && <span className="text-xs text-zinc-500">Total {taka(c.revenue)}</span>}>
        {data ? <RevenueChart key={data.days} daily={data.daily} /> : <div className="h-64 rounded-xl bg-zinc-100 animate-pulse" />}
      </Card>

      {data && (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card title="Top products">
            <BarList rows={data.top_products} sub={(r) => `${r.count} sold`} />
          </Card>
          <Card title="Payment methods">
            <BarList rows={data.payment} sub={(r) => `${r.count} order${r.count === 1 ? '' : 's'}`} />
            <h2 className="text-sm font-semibold text-zinc-900 mt-8 mb-4">Delivery area</h2>
            <BarList rows={data.delivery} sub={(r) => `${r.count} order${r.count === 1 ? '' : 's'}`} />
          </Card>
          <Card title="Order status" action={<a href="#/orders" className="text-xs font-medium text-emerald-700 hover:text-emerald-800">View all →</a>}>
            <ul className="divide-y divide-zinc-100">
              {data.status.map((s) => (
                <li key={s.status} className="flex items-center justify-between py-2.5">
                  <StatusBadge status={s.status} />
                  <span className="text-sm tabular-nums text-zinc-700">
                    {s.count}<span className="text-zinc-400 ml-2">{totalOrders ? Math.round((100 * s.count) / totalOrders) : 0}%</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
      </div>
    </div>
  )
}
