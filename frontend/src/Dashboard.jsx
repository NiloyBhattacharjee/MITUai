import { Fragment, useEffect, useRef, useState } from 'react'
import { api, toast, useLive } from './App.jsx'
import { Bubble, time } from './Simulator.jsx'
import { Drawer, Empty, Icon, PageHeader, STATUS_STYLE, Segmented, Skeleton, Switch, cx, money, plural, statusLabel } from './ui.jsx'

const STATUSES = ['new', 'preparing', 'ready', 'completed', 'cancelled']
const BOOKING_STATUSES = ['booked', 'arrived', 'no_show', 'cancelled']
const CHANNEL = {
  whatsapp: { label: 'WhatsApp', dot: 'bg-[#25d366]' },
  messenger: { label: 'Messenger', dot: 'bg-[#0866ff]' },
  instagram: { label: 'Instagram', dot: 'bg-[#e1306c]' },
  sim: { label: 'Simulator', dot: 'bg-faint' },
}
const channel = (c) => CHANNEL[c.channel] || { label: c.channel, dot: 'bg-faint' }
// Messenger/Instagram ids are long numbers, not phone numbers.
const customerName = (c) => ['messenger', 'instagram'].includes(c.channel) ? `${channel(c).label} ···${c.phone.slice(-4)}` : c.phone

function Avatar({ c }) {
  return (
    <div className={cx('relative w-10 h-10 shrink-0 rounded-full grid place-items-center text-xs font-semibold',
      c.needs_human ? 'bg-warn/15 text-warn' : 'bg-subtle text-muted')}>
      {c.phone.slice(-2)}
      <span className={cx('absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full ring-2 ring-surface', channel(c).dot)} title={channel(c).label} />
    </div>
  )
}

// What the AI did to answer one customer message. Full calls load on open: too big for every chat poll.
function AiSteps({ m }) {
  const [steps, setSteps] = useState(null)
  const load = (e) => e.currentTarget.open && !steps && api(`/api/messages/${m.id}/steps`).then(setSteps).catch(() => {})
  return (
    <details onToggle={load} className="w-fit max-w-[80%] ml-auto text-[11px] text-faint">
      <summary className="cursor-pointer select-none text-right hover:text-muted">
        {m.ai.tools.length ? `AI used ${m.ai.tools.join(', ')}` : 'AI answered without tools'}
        {m.ai.cost_usd != null && ` · $${m.ai.cost_usd.toFixed(4)}`}
      </summary>
      {steps?.map((s, i) => (
        <div key={i} className="mt-1.5 rounded-lg border border-line bg-surface p-2">
          <div className="font-medium text-muted">{s.name} <span className="font-normal text-faint">{s.ms} ms</span></div>
          <pre className="mt-1 whitespace-pre-wrap break-all font-mono">{JSON.stringify(s.args)}</pre>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono">→ {JSON.stringify(s.result, null, 1)}</pre>
        </div>
      ))}
    </details>
  )
}

export function Chats() {
  const [convs, refreshConvs, setConvs] = useLive('/api/conversations', 2000)
  const [sel, setSel] = useState(null)
  const [view, setView] = useState('all')
  const base = sel && `/api/conversations/${encodeURIComponent(sel)}`
  const [msgs, refreshMsgs, setMsgs] = useLive(base && `${base}/messages`, 2000)
  const [reply, setReply] = useState('')
  const box = useRef(null)
  const shownFor = useRef(null)
  const conv = convs?.find((c) => c.id === sel)
  const typing = conv?.bot_enabled && msgs?.at(-1)?.role === 'user'
  const waiting = convs?.filter((c) => c.needs_human).length
  const list = view === 'needs' ? convs?.filter((c) => c.needs_human) : convs

  // Jump to the newest message when a chat opens; follow new messages only if already near the bottom.
  useEffect(() => {
    const el = box.current
    if (!el || !msgs) return
    const opened = shownFor.current !== sel
    if (opened || el.scrollHeight - el.scrollTop - el.clientHeight < 200)
      el.scrollTo({ top: el.scrollHeight, behavior: opened ? 'instant' : 'smooth' })
    shownFor.current = sel
  }, [sel, msgs?.length, typing])

  // Optimistic: show the change now, then sync with the server (which rolls it back if it failed).
  const send = async (e) => {
    e.preventDefault()
    const text = reply.trim()
    if (!text) return
    setReply('')
    setMsgs([...(msgs || []), { id: `tmp-${Date.now()}`, role: 'human', text, created_at: new Date().toISOString(), pending: true }])
    try {
      await api(`${base}/reply`, { method: 'POST', body: JSON.stringify({ text }) })
    } catch (err) {
      toast(err.status === 409 ? JSON.parse(err.message).detail : 'Message not sent. Check the connection and try again.')
      setReply(text)
    }
    refreshMsgs()
    refreshConvs()
  }
  const toggleBot = async () => {
    const enabled = !conv.bot_enabled
    setConvs(convs.map((c) => c.id === sel ? { ...c, bot_enabled: enabled, ...(enabled && { needs_human: false, handoff_reason: null }) } : c))
    try {
      await api(`${base}/bot`, { method: 'POST', body: JSON.stringify({ enabled }) })
    } catch {
      toast('Could not switch AI replies. Try again.')
    }
    refreshConvs()
  }

  return (
    <>
      <PageHeader title="Conversations" sub="Every WhatsApp, Messenger and Instagram chat. Step in when the assistant needs you." />
      <div className="card overflow-hidden grid md:grid-cols-[340px_1fr] h-[calc(100dvh-15rem)] md:h-[calc(100dvh-13.5rem)] min-h-[480px]">
        <div className={cx('min-h-0 flex-col md:border-r border-line', conv ? 'hidden md:flex' : 'flex')}>
          <div className="p-3 border-b border-line">
            <Segmented label="Show" value={view} onChange={setView}
              options={[{ value: 'all', label: 'All', count: convs?.length }, { value: 'needs', label: 'Needs you', count: waiting }]} />
          </div>
          <ul className="flex-1 overflow-y-auto p-2 space-y-0.5">
            {!convs && [0, 1, 2, 3].map((i) => (
              <li key={i} className="flex gap-3 p-3"><Skeleton className="w-10 h-10 rounded-full!" /><div className="flex-1 space-y-2 pt-1"><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-4/5" /></div></li>
            ))}
            {list?.length === 0 && (
              <Empty icon="chats" title={view === 'needs' ? 'Nobody is waiting' : 'No chats yet'}>
                {view === 'needs' ? 'The assistant is handling every chat.' : <>Message your café or try the <a href="#/simulator" className="text-ink underline underline-offset-2">simulator</a>.</>}
              </Empty>
            )}
            {list?.map((c) => (
              <li key={c.id}>
                <button onClick={() => setSel(c.id)}
                  className={cx('w-full text-left flex gap-3 p-3 rounded-xl transition', sel === c.id ? 'bg-subtle' : 'hover:bg-subtle/60')}>
                  <Avatar c={c} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-sm font-medium truncate">{customerName(c)}</span>
                      <span className="text-xs text-faint shrink-0 tabular-nums">{time(c.updated_at)}</span>
                    </div>
                    <div className="text-sm text-muted truncate mt-0.5">
                      {c.last_message?.is_voice && <Icon name="mic" className="inline w-3.5 h-3.5 mr-1 -mt-0.5" />}
                      {c.last_message?.text || (c.last_message?.image_url ? 'Photo' : '')}
                    </div>
                    {c.needs_human && <span className="chip bg-warn/10 text-warn mt-2">Needs you</span>}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {!conv ? (
          <div className="hidden md:grid place-items-center bg-canvas/40">
            <Empty icon="chats" title="Pick a conversation">Chats show up here as customers message you.</Empty>
          </div>
        ) : (
          <div key={sel} className="enter min-h-0 flex flex-col bg-canvas/40">
            <div className="flex items-center justify-between gap-3 px-4 h-16 border-b border-line bg-surface">
              <div className="flex items-center gap-3 min-w-0">
                <button onClick={() => setSel(null)} className="md:hidden btn btn-ghost px-1.5! -ml-1" aria-label="Back to conversations"><Icon name="back" /></button>
                <Avatar c={conv} />
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{customerName(conv)}</div>
                  {conv.needs_human
                    ? <div className="text-xs text-warn truncate">Needs you: {conv.handoff_reason}</div>
                    : <div className="text-xs text-faint truncate">
                        {[channel(conv).label, conv.bot_enabled ? 'AI is replying' : 'You are replying'].join(' · ')}
                      </div>}
                </div>
              </div>
              <Switch checked={conv.bot_enabled} onChange={toggleBot} label={<span className="hidden sm:inline">AI replies</span>} />
            </div>
            <div ref={box} className="flex-1 overflow-y-auto px-4 py-5 space-y-2.5">
              {msgs?.map((m) => <Fragment key={m.id}><Bubble m={m} mine={m.role !== 'user'} />{m.ai && <AiSteps m={m} />}</Fragment>)}
              {typing && (
                <div className="enter flex justify-end" aria-label="AI assistant is typing">
                  <div className="flex gap-1 rounded-2xl rounded-br-md bg-brand-soft px-3.5 py-3">
                    {[0, 150, 300].map((d) => <span key={d} className="w-1.5 h-1.5 rounded-full bg-brand/60 animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                  </div>
                </div>
              )}
            </div>
            {conv.window_open === false ? (
              <p className="px-6 py-4 border-t border-line bg-surface text-sm text-muted text-center">
                The 24-hour reply window has closed. You can reply once the customer messages again.
              </p>
            ) : (
              <form onSubmit={send} className="flex items-center gap-2 p-3 border-t border-line bg-surface">
                <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply as the café…" aria-label="Reply"
                  className="field rounded-full! h-11! px-5!" />
                <button disabled={!reply.trim()} className="btn btn-primary rounded-full! w-11! h-11! px-0!" aria-label="Send"><Icon name="send" /></button>
              </form>
            )}
          </div>
        )}
      </div>
    </>
  )
}

const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
const day = (iso) => new Date(iso).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
const detail = (err, fallback) => { try { const d = JSON.parse(err.message).detail; return typeof d === 'string' ? d : fallback } catch { return fallback } }

// Optimistic status change for an order or booking; the server's note (e.g. "customer not notified") shows as a toast.
async function changeStatus(path, label, rows, setRows, refresh, id, status) {
  setRows(rows.map((r) => r.id === id ? { ...r, status } : r))
  try {
    const r = await api(`${path}/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) })
    if (r.note) toast(r.note)
  } catch {
    toast(`Could not update ${label} #${id}. Try again.`)
  }
  refresh()
}

const StatusSelect = ({ value, options, onChange, label }) => (
  <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}
    className={cx('chip py-1! pr-1 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-brand/40', STATUS_STYLE[value])}>
    {options.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
  </select>
)

export function Orders() {
  const [orders, refresh, setOrders] = useLive('/api/orders', 5000)
  const [filter, setFilter] = useState('all')
  const exportCsv = async () => {
    const res = await fetch('/api/orders/export.csv', { headers: { 'X-Admin-Password': localStorage.getItem('adminPassword') || '' } })
    if (!res.ok) return toast('Export failed. Try again.')
    const a = Object.assign(document.createElement('a'),
      { href: URL.createObjectURL(await res.blob()), download: `orders-${new Date().toISOString().slice(0, 10)}.csv` })
    a.click()
    URL.revokeObjectURL(a.href)
  }
  const all = orders || []
  const shown = filter === 'all' ? all : all.filter((o) => o.status === filter)
  return (
    <>
      <PageHeader title="Orders" sub="Every order the assistant took. Customers are told when it's being made and when it's ready.">
        <button onClick={exportCsv} className="btn btn-secondary"><Icon name="download" /> Export CSV</button>
      </PageHeader>
      <div className="mb-4">
        <Segmented label="Filter by status" value={filter} onChange={setFilter}
          options={['all', ...STATUSES].map((s) => ({ value: s, label: s === 'all' ? 'All' : statusLabel(s), count: orders && (s === 'all' ? all.length : all.filter((o) => o.status === s).length) }))} />
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              {['Order', 'Customer', 'Items', 'Total', 'Status'].map((h) => <th key={h} className="px-5 h-11 font-medium whitespace-nowrap">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {!orders && [0, 1, 2, 3, 4].map((i) => (
              <tr key={i} className="border-b border-line last:border-0">{[16, 32, 40, 14, 20].map((w, j) => <td key={j} className="px-5 py-5"><Skeleton className="h-3" style={{ width: `${w * 4}px` }} /></td>)}</tr>
            ))}
            {orders && shown.length === 0 && <tr><td colSpan="5"><Empty icon="orders" title="No orders here">Orders appear as soon as a customer confirms one.</Empty></td></tr>}
            {shown.map((o) => (
              <tr key={o.id} className="align-top border-b border-line last:border-0 hover:bg-subtle/40 transition-colors">
                <td className="px-5 py-4 whitespace-nowrap">
                  <div className="font-medium tabular-nums">#{o.id}</div>
                  <div className="text-xs text-faint mt-0.5">{day(o.ready_at)}, {clock(o.ready_at)}</div>
                </td>
                <td className="px-5 py-4 min-w-48">
                  <div className="font-medium">{o.customer_name}</div>
                  <div className="text-xs text-muted mt-0.5 tabular-nums">{o.phone} · {o.fulfilment_label}</div>
                  {o.address && <div className="text-xs text-faint mt-0.5 truncate max-w-56" title={o.address}>{o.address}</div>}
                </td>
                <td className="px-5 py-4 min-w-52 space-y-1">
                  {o.items.map((line, i) => <div key={i}>{line}</div>)}
                  {o.notes && <div className="text-xs text-warn">Note: {o.notes}</div>}
                </td>
                <td className="px-5 py-4 whitespace-nowrap font-medium tabular-nums">{money(o.total)}</td>
                <td className="px-5 py-4">
                  <StatusSelect value={o.status} options={STATUSES} label={`Status of order ${o.id}`}
                    onChange={(s) => changeStatus('/api/orders', 'order', orders, setOrders, refresh, o.id, s)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// --- kitchen -------------------------------------------------------------------------------------

const NEXT = { new: ['preparing', 'Start'], preparing: ['ready', 'Ready'], ready: ['completed', 'Done'] }
const COLUMNS = [['new', 'New'], ['preparing', 'Preparing'], ['ready', 'Ready']]
let audio = null
function beep() {
  audio ||= new AudioContext()  // created on the "Sound on" click: browsers only allow audio after a tap
  const osc = audio.createOscillator(), gain = audio.createGain()
  osc.frequency.value = 880
  osc.connect(gain).connect(audio.destination)
  gain.gain.setValueAtTime(0.3, audio.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.5)
  osc.start()
  osc.stop(audio.currentTime + 0.5)
}

const span = (m) => m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ''}`.trim()
function Due({ iso }) {
  const mins = Math.round((new Date(iso) - Date.now()) / 60000)
  return <span className={cx('tabular-nums', mins < 0 ? 'text-danger font-medium' : 'text-muted')}>{clock(iso)} · {mins < 0 ? `${span(-mins)} late` : `in ${span(mins)}`}</span>
}

export function Kitchen() {
  const [orders, refresh, setOrders] = useLive('/api/orders?active=true', 3000)
  const [settings, , setSettings] = useLive('/api/settings', 15000)
  const [sound, setSound] = useState(false)
  const seen = useRef(null)
  const fresh = orders?.filter((o) => o.status === 'new') || []

  // Beep when an order arrives, and show the count in the tab title.
  useEffect(() => {
    if (!orders) return
    const ids = new Set(fresh.map((o) => o.id))
    if (sound && seen.current && [...ids].some((id) => !seen.current.has(id))) beep()
    seen.current = ids
    document.title = fresh.length ? `(${fresh.length}) New orders · Mitu` : 'Mitu Dashboard'
  }, [orders]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { document.title = 'Mitu Dashboard' }, [])

  const move = (o, status) => changeStatus('/api/orders', 'order', orders, setOrders, refresh, o.id, status)
  const togglePause = async () => {
    const orders_paused = !settings?.orders_paused
    setSettings({ orders_paused })
    try {
      await api('/api/settings', { method: 'PUT', body: JSON.stringify({ orders_paused }) })
      toast(orders_paused ? 'Orders paused: the assistant takes bookings and answers questions, but no orders.' : 'Taking orders again.')
    } catch {
      setSettings({ orders_paused: !orders_paused })
      toast('Could not change it. Try again.')
    }
  }
  const soundOn = () => { setSound(!sound); if (!sound) beep() }

  return (
    <>
      <PageHeader title="Kitchen" sub="Orders to make, soonest first. Moving an order on tells the customer.">
        <button onClick={soundOn} aria-pressed={sound} className={cx('btn', sound ? 'btn-secondary' : 'btn-ghost')}>
          <Icon name="bell" /> {sound ? 'Sound on' : 'Sound off'}
        </button>
        <button onClick={togglePause} aria-pressed={!!settings?.orders_paused} className={cx('btn', settings?.orders_paused ? 'btn-primary' : 'btn-secondary')}>
          <Icon name="pause" /> {settings?.orders_paused ? 'Resume orders' : 'Pause orders'}
        </button>
      </PageHeader>
      {settings?.orders_paused && (
        <p className="mb-5 rounded-xl bg-warn/10 text-warn text-sm px-4 py-3">Orders are paused. The assistant is telling customers it can't take orders right now.</p>
      )}
      <div className="grid gap-5 lg:grid-cols-3 items-start">
        {COLUMNS.map(([status, title]) => {
          const col = orders?.filter((o) => o.status === status) || []
          return (
            <section key={status} aria-label={title}>
              <h2 className="mb-3 flex items-center gap-2 text-sm font-medium">{title}<span className="text-faint tabular-nums">{orders && col.length}</span></h2>
              <div className="space-y-3">
                {!orders && <Skeleton className="h-40 rounded-2xl!" />}
                {orders && col.length === 0 && <div className="rounded-2xl border border-dashed border-line px-4 py-8 text-center text-sm text-faint">Nothing here</div>}
                {col.map((o) => (
                  <article key={o.id} className="enter card p-4">
                    <header className="flex items-baseline justify-between gap-3">
                      <span className="font-semibold tabular-nums">#{o.id} · {o.fulfilment_label}</span>
                      <span className="text-xs"><Due iso={o.ready_at} /></span>
                    </header>
                    <div className="mt-1 text-sm text-muted">{o.customer_name} · {o.phone}</div>
                    {o.address && <div className="text-xs text-faint mt-0.5">{o.address}</div>}
                    <ul className="mt-3 space-y-1 text-[15px]">{o.items.map((line, i) => <li key={i}>{line}</li>)}</ul>
                    {o.notes && <p className="mt-2 text-sm text-warn">Note: {o.notes}</p>}
                    <footer className="mt-4 flex items-center justify-between gap-2">
                      <span className="text-sm tabular-nums text-muted">{money(o.total)}</span>
                      <div className="flex gap-2">
                        {o.status === 'new' && <button onClick={() => confirm(`Cancel order #${o.id}? The customer is told.`) && move(o, 'cancelled')} className="btn btn-ghost text-danger!">Cancel</button>}
                        <button onClick={() => move(o, NEXT[o.status][0])} className="btn btn-primary">{NEXT[o.status][1]}</button>
                      </div>
                    </footer>
                  </article>
                ))}
              </div>
            </section>
          )
        })}
      </div>
    </>
  )
}

// --- bookings ------------------------------------------------------------------------------------

export function Bookings() {
  const [rows, refresh, setRows] = useLive('/api/bookings?days=14', 10000)
  const days = []
  for (const b of rows || []) {
    const key = new Date(b.at).toDateString()
    if (days.at(-1)?.key !== key) days.push({ key, label: day(b.at), rows: [] })
    days.at(-1).rows.push(b)
  }
  const today = new Date().toDateString()
  return (
    <>
      <PageHeader title="Bookings" sub="Tables the assistant booked for the next two weeks. Cancelling tells the customer." />
      {!rows && <Skeleton className="h-48 rounded-2xl!" />}
      {rows?.length === 0 && <div className="card"><Empty icon="bookings" title="No bookings yet">Customers can book a table in any chat.</Empty></div>}
      <div className="space-y-6">
        {days.map((d) => {
          const covers = d.rows.filter((b) => b.status !== 'cancelled').reduce((n, b) => n + b.party_size, 0)
          return (
            <section key={d.key} className="card overflow-hidden">
              <header className="flex items-baseline justify-between gap-3 px-5 py-3 border-b border-line bg-subtle/40">
                <h2 className="text-sm font-medium">{d.key === today ? `Today · ${d.label}` : d.label}</h2>
                <span className="text-xs text-muted tabular-nums">{plural(covers, 'guest')}</span>
              </header>
              <ul>
                {d.rows.map((b) => (
                  <li key={b.id} className={cx('flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3 border-b border-line last:border-0', b.status === 'cancelled' && 'opacity-50')}>
                    <span className="w-20 font-semibold tabular-nums whitespace-nowrap">{clock(b.at)}</span>
                    <span className="flex items-center gap-1.5 w-16 text-sm text-muted tabular-nums"><Icon name="users" className="w-4 h-4" />{b.party_size}</span>
                    <div className="min-w-40 flex-1">
                      <div className="text-sm font-medium">{b.customer_name}</div>
                      <div className="text-xs text-muted tabular-nums">{b.phone}{b.notes && <span className="text-warn"> · {b.notes}</span>}</div>
                    </div>
                    <StatusSelect value={b.status} options={BOOKING_STATUSES} label={`Status of booking ${b.id}`}
                      onChange={(s) => changeStatus('/api/bookings', 'booking', rows, setRows, refresh, b.id, s)} />
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>
    </>
  )
}

// --- menu ----------------------------------------------------------------------------------------

const Section = ({ title, hint, children }) => (
  <section className="py-6 first:pt-0 border-b border-line last:border-0">
    <h3 className="text-sm font-medium">{title}</h3>
    {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    <div className="mt-4">{children}</div>
  </section>
)

export function Menu() {
  const [items, refresh, setItems] = useLive('/api/menu', 15000)
  const [open, setOpen] = useState(null)  // null | 'new' | item id
  const [q, setQ] = useState('')
  const replace = (i) => setItems(items.map((x) => x.id === i.id ? i : x))
  const toggle = async (i) => {
    replace({ ...i, available: !i.available })
    try {
      replace(await api(`/api/menu/${i.id}`, { method: 'PATCH', body: JSON.stringify({ available: !i.available }) }))
    } catch {
      toast('Could not save. Try again.')
      refresh()
    }
  }
  const upload = async (id, file) => {
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    try {
      replace(await api(`/api/menu/${id}/photo`, { method: 'POST', body }))
    } catch (err) {
      toast(detail(err, 'Photo upload failed. Try again.'))
    }
  }
  const remove = async (i) => {
    if (!confirm(`Delete ${i.name}? The assistant stops offering it. Past orders keep their details.`)) return
    try {
      await api(`/api/menu/${i.id}`, { method: 'DELETE' })
      setItems(items.filter((x) => x.id !== i.id))
      setOpen(null)
      toast(`${i.name} deleted.`)
    } catch {
      toast('Could not delete it. Try again.')
    }
  }
  const categories = [...new Set((items || []).map((i) => i.category))].sort()
  const needle = q.trim().toLowerCase()
  const shown = (items || []).filter((i) => !needle || [i.name, i.category, i.id].some((t) => t.toLowerCase().includes(needle)))
  const current = items?.find((i) => i.id === open)
  const close = () => setOpen(null)

  return (
    <>
      <PageHeader title="Menu" sub="What the assistant offers: prices, options, dietary info and photos. Mark items sold out for the day.">
        <button onClick={() => setOpen('new')} className="btn btn-primary"><Icon name="plus" /> Add item</button>
      </PageHeader>

      <div className="relative mb-6 max-w-sm">
        <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" className="w-4 h-4" /></span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the menu" aria-label="Search the menu" className="field pl-10!" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {!items && [0, 1, 2, 3].map((i) => <div key={i} className="card overflow-hidden"><Skeleton className="aspect-4/3 rounded-none!" /><div className="p-4 space-y-2"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-3 w-1/3" /></div></div>)}
        {shown.map((i) => (
          <div key={i.id} className={cx('card overflow-hidden flex flex-col transition', !i.available && 'opacity-70')}>
            <button onClick={() => setOpen(i.id)} className="text-left group flex-1">
              <div className="w-full aspect-4/3 bg-subtle grid place-items-center overflow-hidden">
                {i.photo_url
                  ? <img src={i.photo_url} alt="" className="w-full h-full object-cover transition duration-300 group-hover:scale-[1.03]" />
                  : <Icon name="image" className="w-6 h-6 text-faint" />}
              </div>
              <div className="px-4 pt-4">
                <div className="font-medium leading-snug line-clamp-2">{i.name}</div>
                <div className="text-xs text-faint truncate mt-1 capitalize">{i.category}{i.dietary.length > 0 && ` · ${i.dietary.join(', ')}`}</div>
              </div>
            </button>
            <div className="px-4 pb-4 mt-4 flex items-center justify-between gap-2">
              <span className="font-semibold tabular-nums">{money(i.price)}</span>
              <button onClick={() => toggle(i)} aria-pressed={!i.available} title={i.available ? 'Mark sold out' : 'Mark available'}
                className={cx('chip transition', i.available ? 'bg-ok/10 text-ok hover:bg-ok/20' : 'bg-danger/10 text-danger hover:bg-danger/20')}>
                {i.available ? 'Available' : 'Sold out'}
              </button>
            </div>
          </div>
        ))}
      </div>
      {items && shown.length === 0 && <Empty icon="menu" title={q ? 'Nothing matches' : 'No menu items yet'}>{q ? 'Try another name or category.' : 'Add your first item to start taking orders.'}</Empty>}

      {open === 'new' && (
        <Drawer title="New menu item" sub="You can add a photo right after." onClose={close}>
          <ItemForm categories={categories} onCancel={close}
            onSaved={(i) => { setItems([...(items || []), i]); setOpen(i.id); toast(`${i.name} added.`) }} />
        </Drawer>
      )}
      {current && (
        <Drawer title={current.name} sub={`${current.id} · ${current.category}`} onClose={close}>
          <Section title="Photo" hint="JPG or PNG under 5 MB. The assistant sends it when customers ask to see the item.">
            <div className="aspect-video rounded-xl bg-subtle grid place-items-center overflow-hidden mb-3">
              {current.photo_url ? <img src={current.photo_url} alt={current.name} className="w-full h-full object-cover" /> : <Icon name="image" className="w-7 h-7 text-faint" />}
            </div>
            <label className="btn btn-secondary cursor-pointer focus-within:ring-2 focus-within:ring-brand/40">
              <Icon name="image" /> {current.photo_url ? 'Replace photo' : 'Add photo'}
              <input type="file" accept="image/jpeg,image/png" className="sr-only" onChange={(e) => { upload(current.id, e.target.files[0]); e.target.value = '' }} />
            </label>
          </Section>
          <Section title="Today">
            <Switch checked={current.available} onChange={() => toggle(current)} label={current.available ? 'Available' : 'Sold out'} />
          </Section>
          <Section title="Details">
            <ItemForm item={current} categories={categories} onCancel={close} onSaved={(i) => { replace(i); toast(`${i.name} saved.`) }} />
          </Section>
          <Section title="Delete item" hint="The assistant stops offering it straight away. Past orders keep their details.">
            <button onClick={() => remove(current)} className="btn btn-secondary text-danger!"><Icon name="trash" /> Delete {current.name}</button>
          </Section>
        </Drawer>
      )}
    </>
  )
}

const csv = (s) => s.split(',').map((x) => x.trim()).filter(Boolean)

// Add a menu item (item = null) or edit one.
function ItemForm({ item, categories, onSaved, onCancel }) {
  const [f, setF] = useState(item
    ? { name: item.name, category: item.category, price: (item.price / 100).toFixed(2), description: item.description,
        options: item.options.join('\n'), dietary: item.dietary.join(', '), allergens: item.allergens.join(', '), aliases: item.aliases.join(', ') }
    : { name: '', category: '', price: '', description: '', options: '', dietary: '', allergens: '', aliases: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => { setF({ ...f, [k]: e.target.value }); setError('') }
  const submit = async (e) => {
    e.preventDefault()
    const body = { ...f, price: Number(f.price), options: f.options.split('\n').map((l) => l.trim()).filter(Boolean),
      dietary: csv(f.dietary), allergens: csv(f.allergens), aliases: csv(f.aliases) }
    setSaving(true)
    try {
      onSaved(await api(item ? `/api/menu/${item.id}` : '/api/menu', { method: item ? 'PATCH' : 'POST', body: JSON.stringify(body) }))
    } catch (err) {
      setError(err.status === 422 ? detail(err, 'Check the fields: the price must be above 0.') : 'Could not save. Try again.')
    }
    setSaving(false)
  }
  const field = (k, label, props = {}, hint) => (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input value={f[k]} onChange={set(k)} {...props} className="field mt-1.5" />
      {hint && <span className="block mt-1.5 text-xs text-faint">{hint}</span>}
    </label>
  )
  const area = (k, label, props, hint) => (
    <label className="block sm:col-span-2">
      <span className="text-sm font-medium">{label}</span>
      <textarea value={f[k]} onChange={set(k)} {...props} className="field mt-1.5 h-auto! py-2.5 leading-relaxed" />
      {hint && <span className="block mt-1.5 text-xs text-faint">{hint}</span>}
    </label>
  )
  return (
    <form onSubmit={submit} className="grid sm:grid-cols-2 gap-4">
      <div className="sm:col-span-2">{field('name', 'Name', { required: true, placeholder: 'Iced Mocha', autoFocus: !item })}</div>
      {field('category', 'Category', { required: true, list: 'categories', placeholder: 'coffee' })}
      <datalist id="categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      {field('price', 'Price', { required: true, type: 'number', min: 0.01, step: 0.01, inputMode: 'decimal' })}
      {area('description', 'Description', { rows: 2, placeholder: 'Double shot, chocolate, cold milk and ice.' })}
      {area('options', 'Options', { rows: 4, placeholder: 'Size*: Regular, Large +0.80\nMilk: Full cream, Oat +0.80\nExtras (any): Extra shot +0.60' },
        'One group per line. * = the customer must pick one, (any) = they can pick several, +0.80 = extra cost.')}
      {field('dietary', 'Dietary', { placeholder: 'vegetarian, gluten-free' }, 'The assistant only repeats what you write here.')}
      {field('allergens', 'Allergens', { placeholder: 'milk, nuts' })}
      <div className="sm:col-span-2">{field('aliases', 'Other names customers use', { placeholder: 'mocha, iced coffee' }, 'Optional. Helps the assistant find it.')}</div>
      {error && <p role="alert" className="sm:col-span-2 text-sm text-danger">{error}</p>}
      <div className="sm:col-span-2 flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className="btn btn-ghost">Cancel</button>
        <button disabled={saving} className="btn btn-primary">{saving ? 'Saving…' : item ? 'Save changes' : 'Add item'}</button>
      </div>
    </form>
  )
}
