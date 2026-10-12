import { Fragment, useEffect, useRef, useState } from 'react'
import { api, toast, useLive } from './App.jsx'
import { Bubble, time } from './Simulator.jsx'
import { Drawer, Empty, Icon, PageHeader, STATUS_STYLE, Segmented, Skeleton, Switch, cx, taka } from './ui.jsx'

const STATUSES = ['new', 'confirmed', 'shipped', 'delivered', 'cancelled']
const PAYMENT_LABEL = { cod: 'Cash on delivery', bkash: 'bKash', nagad: 'Nagad' }
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
                {view === 'needs' ? 'The assistant is handling every chat.' : <>Message your shop or try the <a href="#/simulator" className="text-ink underline underline-offset-2">simulator</a>.</>}
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
                        {[channel(conv).label, conv.language, conv.bot_enabled ? 'AI is replying' : 'You are replying'].filter(Boolean).join(' · ')}
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
                <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply as the shop…" aria-label="Reply"
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

export function Orders() {
  const [orders, refresh, setOrders] = useLive('/api/orders', 5000)
  const [filter, setFilter] = useState('all')
  const setStatus = async (id, status) => {
    setOrders(orders.map((o) => o.id === id ? { ...o, status } : o))
    try {
      const r = await api(`/api/orders/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) })
      if (r.note) toast(r.note)
    } catch (err) {
      toast(err.status === 409 ? JSON.parse(err.message).detail : `Could not update order #${id}. Try again.`)
    }
    refresh()
  }
  const setPaid = async (id, paid) => {
    setOrders(orders.map((o) => o.id === id ? { ...o, paid } : o))
    try {
      const r = await api(`/api/orders/${id}/paid`, { method: 'POST', body: JSON.stringify({ paid }) })
      if (r.note) toast(r.note)
    } catch {
      toast(`Could not update payment for order #${id}. Try again.`)
    }
    refresh()
  }
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
      <PageHeader title="Orders" sub="Every order the assistant took. Update the status as you ship; the customer is told.">
        <button onClick={exportCsv} className="btn btn-secondary"><Icon name="download" /> Export CSV</button>
      </PageHeader>
      <div className="mb-4">
        <Segmented label="Filter by status" value={filter} onChange={setFilter}
          options={['all', ...STATUSES].map((s) => ({ value: s, label: s[0].toUpperCase() + s.slice(1), count: orders && (s === 'all' ? all.length : all.filter((o) => o.status === s).length) }))} />
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              {['Order', 'Customer', 'Items', 'Payment', 'Total', 'Status'].map((h) => <th key={h} className="px-5 h-11 font-medium whitespace-nowrap">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {!orders && [0, 1, 2, 3, 4].map((i) => (
              <tr key={i} className="border-b border-line last:border-0">{[16, 32, 40, 24, 14, 20].map((w, j) => <td key={j} className="px-5 py-5"><Skeleton className="h-3" style={{ width: `${w * 4}px` }} /></td>)}</tr>
            ))}
            {orders && shown.length === 0 && <tr><td colSpan="6"><Empty icon="orders" title="No orders here">Orders appear as soon as a customer confirms one.</Empty></td></tr>}
            {shown.map((o) => (
              <tr key={o.id} className="align-top border-b border-line last:border-0 hover:bg-subtle/40 transition-colors">
                <td className="px-5 py-4 whitespace-nowrap">
                  <div className="font-medium tabular-nums">#{o.id}</div>
                  <div className="text-xs text-faint mt-0.5">{new Date(o.created_at).toLocaleDateString([], { day: 'numeric', month: 'short' })}</div>
                </td>
                <td className="px-5 py-4 min-w-48" title={o.address}>
                  <div className="font-medium">{o.customer_name}</div>
                  <div className="text-xs text-muted mt-0.5 tabular-nums">{o.phone} · {o.delivery_area === 'inside_dhaka' ? 'Inside Dhaka' : 'Outside Dhaka'}</div>
                  <div className="text-xs text-faint mt-0.5 truncate max-w-56">{o.address}</div>
                </td>
                <td className="px-5 py-4 min-w-52 space-y-1">
                  {o.items.map((l, i) => (
                    <div key={i}><span className="tabular-nums">{l.quantity}×</span> {l.name} <span className="text-faint">· {l.size} · {l.colour}</span></div>
                  ))}
                </td>
                <td className="px-5 py-4 whitespace-nowrap">
                  <div>{PAYMENT_LABEL[o.payment_method] || o.payment_method}</div>
                  {o.payment_method !== 'cod' && <>
                    <div className={cx('text-xs mt-0.5', o.payment_ref ? 'font-mono text-muted' : 'text-faint')}>{o.payment_ref || 'No TrxID yet'}</div>
                    <button onClick={() => setPaid(o.id, !o.paid)} aria-pressed={o.paid}
                      className={cx('chip mt-2 transition', o.paid ? 'bg-ok/10 text-ok' : 'border border-line text-muted hover:text-ink hover:bg-subtle')}>
                      {o.paid ? <><Icon name="check" className="w-3 h-3" /> Paid</> : 'Mark paid'}
                    </button>
                  </>}
                </td>
                <td className="px-5 py-4 whitespace-nowrap font-medium tabular-nums">{taka(o.total)}</td>
                <td className="px-5 py-4">
                  <select value={o.status} onChange={(e) => setStatus(o.id, e.target.value)} aria-label={`Status of order ${o.id}`}
                    className={cx('chip py-1! pr-1 capitalize cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-brand/40', STATUS_STYLE[o.status])}>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// --- products ----------------------------------------------------------------------------------

const LOW_STOCK = 3  // same as tools.LOW_STOCK

function StockChip({ p }) {
  if (!p.total_stock) return <span className="chip bg-danger/10 text-danger">Out of stock</span>
  if (p.variants.some((v) => v.stock <= LOW_STOCK)) return <span className="chip bg-warn/10 text-warn">Low stock</span>
  return <span className="chip bg-ok/10 text-ok">In stock</span>
}

// Saves when the field loses focus (or on Enter), only if the value changed.
function NumberCell({ value, label, onSave, className }) {
  const [v, setV] = useState(String(value))
  useEffect(() => setV(String(value)), [value])
  const save = () => {
    const n = Number(v)
    if (v === '' || !Number.isInteger(n) || n < 0) return setV(String(value))
    if (n !== value) onSave(n)
  }
  return (
    <input type="number" min="0" inputMode="numeric" value={v} aria-label={label}
      onChange={(e) => setV(e.target.value)} onBlur={save} onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
      className={cx('field tabular-nums', className)} />
  )
}

// Stock for every size (columns) and colour (rows).
function StockGrid({ p, onSave }) {
  const stock = Object.fromEntries(p.variants.map((v) => [`${v.size}|${v.colour}`, v.stock]))
  return (
    <div className="overflow-x-auto -mx-1 px-1">
      <table className="text-sm">
        <thead>
          <tr><th />{p.sizes.map((s) => <th key={s} className="px-1 pb-2 text-xs font-medium text-muted text-center">{s}</th>)}</tr>
        </thead>
        <tbody>
          {p.colours.map((c) => (
            <tr key={c}>
              <td className="pr-3 py-1 text-muted whitespace-nowrap">{c}</td>
              {p.sizes.map((s) => {
                const n = stock[`${s}|${c}`] ?? 0
                return (
                  <td key={s} className="p-1">
                    <NumberCell value={n} label={`Stock of ${s}, ${c}`} onSave={(v) => onSave(s, c, v)}
                      className={cx('w-16! h-9! px-2! text-center', n === 0 ? 'border-danger/40! text-danger' : n <= LOW_STOCK && 'border-warn/50! text-warn')} />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const Section = ({ title, hint, children }) => (
  <section className="py-6 first:pt-0 border-b border-line last:border-0">
    <h3 className="text-sm font-medium">{title}</h3>
    {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    <div className="mt-4">{children}</div>
  </section>
)

export function Products() {
  const [products, refresh, setProducts] = useLive('/api/products', 10000)
  const [open, setOpen] = useState(null)  // null | 'new' | product id
  const [q, setQ] = useState('')
  const replace = (p) => setProducts(products.map((x) => x.id === p.id ? p : x))
  const saveStock = async (id, size, colour, stock) => {
    try {
      replace(await api(`/api/products/${id}/stock`, { method: 'PATCH', body: JSON.stringify({ size, colour, stock }) }))
    } catch {
      toast('Could not save stock. Try again.')
      refresh()
    }
  }
  const upload = async (id, file) => {
    if (!file) return
    const body = new FormData()
    body.append('file', file)
    try {
      replace(await api(`/api/products/${id}/photo`, { method: 'POST', body }))
    } catch (err) {
      toast(err.status === 400 ? JSON.parse(err.message).detail : 'Photo upload failed. Try again.')
    }
  }
  const remove = async (p) => {
    if (!confirm(`Delete ${p.name_en}? The assistant will stop selling it. Past orders keep their details.`)) return
    try {
      await api(`/api/products/${p.id}`, { method: 'DELETE' })
      setProducts(products.filter((x) => x.id !== p.id))
      setOpen(null)
      toast(`${p.name_en} deleted.`)
    } catch {
      toast('Could not delete the product. Try again.')
    }
  }
  const categories = [...new Set((products || []).map((p) => p.category))].sort()
  const needle = q.trim().toLowerCase()
  const shown = (products || []).filter((p) => !needle || [p.name_en, p.name_bn, p.category, p.id].some((t) => t?.toLowerCase().includes(needle)))
  const current = products?.find((p) => p.id === open)
  const close = () => setOpen(null)

  return (
    <>
      <PageHeader title="Products" sub="What the assistant sells: prices, stock by size and colour, and photos.">
        <button onClick={() => setOpen('new')} className="btn btn-primary"><Icon name="plus" /> Add product</button>
      </PageHeader>

      <div className="relative mb-6 max-w-sm">
        <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-faint"><Icon name="search" className="w-4 h-4" /></span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search products" aria-label="Search products" className="field pl-10!" />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {!products && [0, 1, 2, 3].map((i) => <div key={i} className="card overflow-hidden"><Skeleton className="aspect-4/3 rounded-none!" /><div className="p-4 space-y-2"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-3 w-1/3" /></div></div>)}
        {shown.map((p) => (
          <button key={p.id} onClick={() => setOpen(p.id)}
            className="card overflow-hidden text-left group flex flex-col transition hover:-translate-y-0.5 hover:shadow-xl hover:shadow-black/5">
            <div className="w-full aspect-4/3 bg-subtle grid place-items-center overflow-hidden">
              {p.photo_url
                ? <img src={p.photo_url} alt="" className="w-full h-full object-cover transition duration-300 group-hover:scale-[1.03]" />
                : <Icon name="image" className="w-6 h-6 text-faint" />}
            </div>
            <div className="p-4">
              <div className="font-medium leading-snug line-clamp-2">{p.name_en}</div>
              <div className="text-xs text-faint truncate mt-1">{p.name_bn || p.category}</div>
              <div className="mt-4 flex items-center justify-between gap-2">
                <span className="font-semibold tabular-nums">{taka(p.price_bdt)}</span>
                <span className="flex items-center gap-2 text-xs text-faint tabular-nums">{p.total_stock > 0 && p.total_stock}<StockChip p={p} /></span>
              </div>
            </div>
          </button>
        ))}
      </div>
      {products && shown.length === 0 && <Empty icon="products" title={q ? 'No matching products' : 'No products yet'}>{q ? 'Try another name or category.' : 'Add your first product to start selling.'}</Empty>}

      {open === 'new' && (
        <Drawer title="New product" sub="You can add a photo and adjust stock right after." onClose={close}>
          <ProductForm categories={categories} onCancel={close}
            onSaved={(p) => { setProducts([...(products || []), p]); setOpen(p.id); toast(`${p.name_en} added as ${p.id}.`) }} />
        </Drawer>
      )}
      {current && (
        <Drawer title={current.name_en} sub={`${current.id} · ${current.category}`} onClose={close}>
          <Section title="Photo" hint="JPG or PNG under 5 MB. The assistant sends it when customers ask to see the product.">
            <div className="aspect-video rounded-xl bg-subtle grid place-items-center overflow-hidden mb-3">
              {current.photo_url ? <img src={current.photo_url} alt={current.name_en} className="w-full h-full object-cover" /> : <Icon name="image" className="w-7 h-7 text-faint" />}
            </div>
            <label className="btn btn-secondary cursor-pointer focus-within:ring-2 focus-within:ring-brand/40">
              <Icon name="image" /> {current.photo_url ? 'Replace photo' : 'Add photo'}
              <input type="file" accept="image/jpeg,image/png" className="sr-only" onChange={(e) => { upload(current.id, e.target.files[0]); e.target.value = '' }} />
            </label>
          </Section>
          <Section title="Stock" hint={`Saved as you type. Amber means ${LOW_STOCK} or fewer left; you get an email when an order gets there.`}>
            <StockGrid p={current} onSave={(size, colour, stock) => saveStock(current.id, size, colour, stock)} />
          </Section>
          <Section title="Details">
            <ProductForm product={current} categories={categories} onCancel={close}
              onSaved={(p) => { replace(p); toast(`${p.name_en} saved.`) }} />
          </Section>
          <Section title="Delete product" hint="The assistant stops selling it straight away. Past orders keep their details.">
            <button onClick={() => remove(current)} className="btn btn-secondary text-danger!"><Icon name="trash" /> Delete {current.name_en}</button>
          </Section>
        </Drawer>
      )}
    </>
  )
}

const csv = (s) => s.split(',').map((x) => x.trim()).filter(Boolean)

// Add a product (product = null) or edit one: name, category, price, sizes, colours, other names.
function ProductForm({ product, categories, onSaved, onCancel }) {
  const [f, setF] = useState(product
    ? { name_en: product.name_en, name_bn: product.name_bn, category: product.category, price: String(product.price_bdt),
        sizes: product.sizes.join(', '), colours: product.colours.join(', '), aliases: product.aliases.join(', ') }
    : { name_en: '', name_bn: '', category: '', price: '', stock: '', sizes: '', colours: '', aliases: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => { setF({ ...f, [k]: e.target.value }); setError('') }
  const submit = async (e) => {
    e.preventDefault()
    const body = { ...f, price: Number(f.price), sizes: csv(f.sizes), colours: csv(f.colours), aliases: csv(f.aliases) }
    if (!product) body.stock = Number(f.stock)
    if (!body.sizes.length || !body.colours.length) return setError('Add at least one size and one colour.')
    setSaving(true)
    try {
      onSaved(await api(product ? `/api/products/${product.id}` : '/api/products',
        { method: product ? 'PATCH' : 'POST', body: JSON.stringify(body) }))
    } catch (err) {
      setError(err.status === 422 ? 'Check the fields: price must be above 0 and stock 0 or more.' : 'Could not save the product. Try again.')
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
  return (
    <form onSubmit={submit} className="grid sm:grid-cols-2 gap-4">
      {field('name_en', 'Name (English)', { required: true, placeholder: 'Maroon Silk Panjabi', autoFocus: !product })}
      {field('name_bn', 'Name (Bangla)', { placeholder: 'মেরুন সিল্ক পাঞ্জাবি' })}
      {field('category', 'Category', { required: true, list: 'categories', placeholder: 'panjabi' })}
      <datalist id="categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
      {field('price', 'Price (৳)', { required: true, type: 'number', min: 1, inputMode: 'numeric' })}
      {field('sizes', 'Sizes', { required: true, placeholder: 'M, L, XL' }, 'Separate with commas.')}
      {field('colours', 'Colours', { required: true, placeholder: 'Maroon, Black' }, 'Separate with commas.')}
      {!product && field('stock', 'Starting stock', { required: true, type: 'number', min: 0, inputMode: 'numeric' }, 'For each size and colour.')}
      <div className="sm:col-span-2">
        {field('aliases', 'Other names customers type', { placeholder: 'panjabi, punjabi, silk panjabi' },
          product ? 'Optional. New sizes or colours start with 0 stock; removed ones lose theirs.' : 'Optional. Banglish spellings help the assistant find it.')}
      </div>
      {error && <p role="alert" className="sm:col-span-2 text-sm text-danger">{error}</p>}
      <div className="sm:col-span-2 flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className="btn btn-ghost">Cancel</button>
        <button disabled={saving} className="btn btn-primary">{saving ? 'Saving…' : product ? 'Save changes' : 'Add product'}</button>
      </div>
    </form>
  )
}
