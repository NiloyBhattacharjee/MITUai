import { useEffect, useRef, useState } from 'react'
import { api, toast, useLive } from './App.jsx'
import { Card, STATUS_STYLE, taka } from './Analytics.jsx'
import { Bubble, time } from './Simulator.jsx'

const STATUSES = ['new', 'confirmed', 'shipped', 'delivered', 'cancelled']
const Tag = ({ className = 'bg-zinc-100 text-zinc-600', children }) => (
  <span className={`px-1.5 py-0.5 rounded-md text-[10px] font-medium ${className}`}>{children}</span>
)
const Skeleton = ({ className, style }) => <div style={style} className={`rounded-lg bg-zinc-100 animate-pulse ${className}`} />
const Title = ({ children, sub }) => (
  <div className="mb-6">
    <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">{children}</h1>
    <p className="text-sm text-zinc-500 mt-1">{sub}</p>
  </div>
)

export function Chats() {
  const [convs, refreshConvs, setConvs] = useLive('/api/conversations', 2000)
  const [sel, setSel] = useState(null)
  const base = sel && `/api/conversations/${encodeURIComponent(sel)}`
  const [msgs, refreshMsgs, setMsgs] = useLive(base && `${base}/messages`, 2000)
  const [reply, setReply] = useState('')
  const box = useRef(null)
  const shownFor = useRef(null)
  const conv = convs?.find((c) => c.id === sel)
  const typing = conv?.bot_enabled && msgs?.at(-1)?.role === 'user'

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
      <Title sub="Live WhatsApp, Messenger and Instagram chats. Chats that need a person are highlighted.">Conversations</Title>
      <div className="grid md:grid-cols-[320px_1fr] gap-4 h-[calc(100vh-13rem)] min-h-[420px]">
        <ul className={`bg-white rounded-2xl ring-1 ring-zinc-950/5 shadow-sm overflow-y-auto p-2 space-y-1 ${conv ? 'hidden md:block' : ''}`}>
          {!convs && [0, 1, 2, 3].map((i) => (
            <li key={i} className="flex gap-3 p-3"><Skeleton className="w-10 h-10 !rounded-full" /><div className="flex-1 space-y-2 pt-1"><Skeleton className="h-3 w-1/2" /><Skeleton className="h-3 w-4/5" /></div></li>
          ))}
          {convs?.length === 0 && <li className="p-6 text-sm text-zinc-500 text-center">No chats yet. Message your WhatsApp number or try the <a href="#/simulator" className="text-emerald-700 underline">simulator</a>.</li>}
          {convs?.map((c) => (
            <li key={c.id}>
              <button onClick={() => setSel(c.id)}
                className={`w-full text-left p-3 rounded-xl transition ${sel === c.id ? 'bg-zinc-100' : 'hover:bg-zinc-50'}`}>
                <div className="flex items-center gap-3">
                  <div className={`relative w-10 h-10 shrink-0 rounded-full grid place-items-center text-sm font-semibold ${c.needs_human ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}`}>
                    {c.phone.slice(-2)}
                    {c.needs_human && <span className="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-amber-500 ring-2 ring-white" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex justify-between gap-2 text-sm">
                      <span className="font-medium text-zinc-900 truncate">{c.phone}</span>
                      <span className="text-xs text-zinc-400 shrink-0">{time(c.updated_at)}</span>
                    </div>
                    <div className="text-xs text-zinc-500 truncate mt-0.5">{c.last_message?.is_voice && '🎤 '}{c.last_message?.text}</div>
                  </div>
                </div>
                <div className="flex gap-1 mt-2 ml-13">
                  <Tag>{c.channel === 'sim' ? 'simulator' : c.channel}</Tag>
                  {c.language && <Tag className="bg-sky-50 text-sky-700">{c.language}</Tag>}
                  {c.needs_human && <Tag className="bg-amber-100 text-amber-800">needs human</Tag>}
                  {!c.bot_enabled && <Tag>bot off</Tag>}
                  {c.window_open === false && <Tag>24h closed</Tag>}
                </div>
              </button>
            </li>
          ))}
        </ul>

        {!conv ? (
          <div className="hidden md:grid bg-white rounded-2xl ring-1 ring-zinc-950/5 shadow-sm place-items-center text-zinc-400 text-sm">Select a conversation</div>
        ) : (
          <div key={sel} className="enter bg-[#efeae2] rounded-2xl ring-1 ring-zinc-950/5 shadow-sm flex flex-col overflow-hidden">
            <div className="bg-white px-4 py-3 flex items-center justify-between gap-3 border-b border-zinc-100">
              <div className="flex items-center gap-3 min-w-0">
                <button onClick={() => setSel(null)} className="md:hidden text-zinc-500" aria-label="Back to conversations">←</button>
                <div className="min-w-0">
                  <div className="font-medium text-zinc-900">{conv.phone}</div>
                  {conv.needs_human
                    ? <div className="text-xs text-amber-700 truncate">Handed off: {conv.handoff_reason}</div>
                    : <div className="text-xs text-zinc-400">{conv.bot_enabled ? 'AI assistant is replying' : 'You are replying'}</div>}
                </div>
              </div>
              <button role="switch" aria-checked={conv.bot_enabled} onClick={toggleBot}
                className="flex items-center gap-2 text-sm text-zinc-600 shrink-0">
                AI replies
                <span className={`w-9 h-5 rounded-full p-0.5 transition ${conv.bot_enabled ? 'bg-emerald-500' : 'bg-zinc-300'}`}>
                  <span className={`block w-4 h-4 rounded-full bg-white shadow transition ${conv.bot_enabled ? 'translate-x-4' : ''}`} />
                </span>
              </button>
            </div>
            <div ref={box} className="flex-1 overflow-y-auto p-4 space-y-2">
              {msgs?.map((m) => <Bubble key={m.id} m={m} mine={m.role !== 'user'} />)}
              {typing && (
                <div className="enter flex justify-end" aria-label="AI assistant is typing">
                  <div className="flex gap-1 rounded-lg bg-[#d9fdd3] px-3 py-2.5 shadow-sm">
                    {[0, 150, 300].map((d) => <span key={d} className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
                  </div>
                </div>
              )}
            </div>
            {conv.window_open === false ? (
              <p className="p-4 bg-white border-t border-zinc-100 text-sm text-zinc-500 text-center">
                The 24-hour reply window has closed. You can reply once the customer messages again.
              </p>
            ) : (
              <form onSubmit={send} className="flex gap-2 p-3 bg-white border-t border-zinc-100">
                <input value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply as the shop…"
                  className="flex-1 rounded-xl px-4 py-2 bg-zinc-50 ring-1 ring-zinc-200 focus:ring-2 focus:ring-emerald-500 outline-none text-sm" />
                <button disabled={!reply.trim()} className="bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 text-white rounded-xl px-5 text-sm font-medium transition">Send</button>
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
      <div className="flex items-start justify-between gap-4">
        <Title sub="Every order the assistant took. Update the status as you ship.">Orders</Title>
        <button onClick={exportCsv} className="shrink-0 bg-white hover:bg-zinc-50 ring-1 ring-zinc-200 text-zinc-700 rounded-xl px-4 py-2 text-sm font-medium transition">
          Export to Excel (CSV)
        </button>
      </div>
      <div className="flex gap-2 mb-4 overflow-x-auto">
        {['all', ...STATUSES].map((s) => (
          <button key={s} onClick={() => setFilter(s)} aria-pressed={filter === s}
            className={`px-3 py-1.5 rounded-lg text-sm capitalize whitespace-nowrap transition ${filter === s ? 'bg-zinc-900 text-white' : 'bg-white text-zinc-600 ring-1 ring-zinc-950/5 hover:text-zinc-900'}`}>
            {s} {orders && <span className="opacity-60">{s === 'all' ? all.length : all.filter((o) => o.status === s).length}</span>}
          </button>
        ))}
      </div>
      <Card className="!p-0 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500 border-b border-zinc-100">
            <tr>{['Order', 'Customer', 'Items', 'Delivery', 'Payment', 'Total', 'Status'].map((h) => <th key={h} className="px-5 py-3 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {!orders && [0, 1, 2, 3, 4].map((i) => (
              <tr key={i}>{[16, 28, 40, 24, 12, 14, 20].map((w, j) => <td key={j} className="px-5 py-5"><Skeleton className="h-3" style={{ width: `${w * 4}px` }} /></td>)}</tr>
            ))}
            {orders && shown.length === 0 && <tr><td colSpan="7" className="px-5 py-10 text-center text-zinc-400">No orders.</td></tr>}
            {shown.map((o) => (
              <tr key={o.id} className="align-top hover:bg-zinc-50/60">
                <td className="px-5 py-4">
                  <div className="font-medium text-zinc-900">#{o.id}</div>
                  <div className="text-xs text-zinc-400 whitespace-nowrap">{new Date(o.created_at).toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' })}</div>
                </td>
                <td className="px-5 py-4">{o.customer_name}<div className="text-xs text-zinc-400">{o.phone}</div></td>
                <td className="px-5 py-4 text-zinc-700">{o.items.map((l, i) => <div key={i}>{l.quantity}× {l.name} <span className="text-zinc-400">({l.size}, {l.colour})</span></div>)}</td>
                <td className="px-5 py-4 text-xs text-zinc-600 max-w-56">{o.delivery_area.replace('_', ' ')}<div className="text-zinc-400">{o.address}</div></td>
                <td className="px-5 py-4 text-xs text-zinc-600">
                  <div className="uppercase">{o.payment_method}</div>
                  {o.payment_method !== 'cod' && <>
                    <div className="text-zinc-400 font-mono mt-0.5">{o.payment_ref ? `TrxID ${o.payment_ref}` : 'no TrxID yet'}</div>
                    <button onClick={() => setPaid(o.id, !o.paid)} aria-pressed={o.paid}
                      className={`mt-1.5 rounded-full px-2 py-0.5 font-medium ring-1 ring-inset transition ${o.paid
                        ? 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' : 'bg-white text-zinc-600 ring-zinc-300 hover:bg-zinc-50'}`}>
                      {o.paid ? 'Paid ✓' : 'Mark paid'}
                    </button>
                  </>}
                </td>
                <td className="px-5 py-4 whitespace-nowrap font-medium text-zinc-900 tabular-nums">{taka(o.total)}</td>
                <td className="px-5 py-4">
                  <select value={o.status} onChange={(e) => setStatus(o.id, e.target.value)} aria-label={`Status of order ${o.id}`}
                    className={`text-xs font-medium capitalize rounded-full pl-2.5 pr-1 py-1 ring-1 ring-inset cursor-pointer transition-colors ${STATUS_STYLE[o.status]}`}>
                    {STATUSES.map((s) => <option key={s} value={s} className="bg-white text-zinc-900">{s}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  )
}

// Saves when the field loses focus (or on Enter), only if the value changed.
function NumberCell({ value, label, onSave, className = 'w-24' }) {
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
      className={`rounded-lg px-2.5 py-1.5 bg-zinc-50 ring-1 ring-zinc-200 focus:ring-2 focus:ring-emerald-500 focus:bg-white outline-none tabular-nums ${className}`} />
  )
}

const LOW_STOCK = 3  // same as tools.LOW_STOCK
const stockStyle = (n) => n === 0 ? '!ring-red-300 text-red-700' : n <= LOW_STOCK ? '!ring-amber-300 text-amber-800' : ''

// Stock for every size (columns) and colour (rows).
function StockGrid({ p, onSave }) {
  const stock = Object.fromEntries(p.variants.map((v) => [`${v.size}|${v.colour}`, v.stock]))
  return (
    <table className="text-xs">
      <thead>
        <tr><th />{p.sizes.map((s) => <th key={s} className="px-1 pb-1 font-medium text-zinc-500 text-center">{s}</th>)}</tr>
      </thead>
      <tbody>
        {p.colours.map((c) => (
          <tr key={c}>
            <td className="pr-2 text-zinc-500 whitespace-nowrap">{c}</td>
            {p.sizes.map((s) => {
              const n = stock[`${s}|${c}`] ?? 0
              return (
                <td key={s} className="p-0.5">
                  <NumberCell value={n} label={`Stock of ${p.name_en}, ${s}, ${c}`} onSave={(v) => onSave(s, c, v)}
                    className={`w-14 !px-1.5 !py-1 text-center ${stockStyle(n)}`} />
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function Products() {
  const [products, refresh, setProducts] = useLive('/api/products', 10000)
  const [form, setForm] = useState(null)  // null | 'new' | product being edited
  const replace = (p) => setProducts(products.map((x) => x.id === p.id ? p : x))
  const save = async (path, body, what) => {
    try {
      replace(await api(path, { method: 'PATCH', body: JSON.stringify(body) }))
    } catch {
      toast(`Could not save ${what}. Try again.`)
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
      setForm(null)
      toast(`${p.name_en} deleted.`)
    } catch {
      toast('Could not delete the product. Try again.')
    }
  }
  const categories = [...new Set((products || []).map((p) => p.category))].sort()
  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <Title sub="Prices and stock per size and colour that the assistant quotes. Changes apply to the next message.">Products</Title>
        {!form && <button onClick={() => setForm('new')} className="shrink-0 bg-zinc-900 hover:bg-zinc-800 text-white rounded-xl px-4 py-2 text-sm font-medium transition">+ Add product</button>}
      </div>
      {form && <ProductForm key={form.id || 'new'} product={form === 'new' ? null : form} categories={categories}
        onClose={() => setForm(null)} onDelete={remove}
        onSaved={(p, added) => {
          setProducts(added ? [...(products || []), p] : products.map((x) => x.id === p.id ? p : x))
          setForm(null)
          toast(added ? `${p.name_en} added as ${p.id}. Click its photo box to add a picture.` : `${p.name_en} saved.`)
        }} />}
      <Card className="!p-0 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500 border-b border-zinc-100">
            <tr>{['Photo', 'Product', 'Price (৳)', 'Stock by size and colour', ''].map((h, i) => <th key={i} className="px-5 py-3 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {!products && [0, 1, 2, 3].map((i) => (
              <tr key={i}>{[12, 40, 20, 40, 10].map((w, j) => <td key={j} className="px-5 py-5"><Skeleton className="h-3" style={{ width: `${w * 4}px` }} /></td>)}</tr>
            ))}
            {products?.map((p) => (
              <tr key={p.id} className="align-top hover:bg-zinc-50/60">
                <td className="px-5 py-3">
                  <label className="block w-14 h-14 rounded-lg overflow-hidden ring-1 ring-zinc-200 bg-zinc-50 cursor-pointer hover:ring-emerald-500 transition focus-within:ring-2 focus-within:ring-emerald-500"
                    title={p.photo_url ? 'Replace photo' : 'Add photo'}>
                    {p.photo_url
                      ? <img src={p.photo_url} alt={p.name_en} className="w-full h-full object-cover" />
                      : <span className="w-full h-full grid place-items-center text-[10px] text-zinc-400">+ photo</span>}
                    <input type="file" accept="image/jpeg,image/png" className="sr-only" aria-label={`Photo for ${p.name_en}`}
                      onChange={(e) => { upload(p.id, e.target.files[0]); e.target.value = '' }} />
                  </label>
                </td>
                <td className="px-5 py-3">
                  <div className="font-medium text-zinc-900">{p.name_en}</div>
                  <div className="text-xs text-zinc-400">{p.id} · {p.name_bn} · {p.category}</div>
                  <div className={`text-xs mt-1 ${p.total_stock === 0 ? 'text-red-600' : 'text-zinc-500'}`}>{p.total_stock} in stock</div>
                </td>
                <td className="px-5 py-3">
                  <NumberCell value={p.price_bdt} label={`Price of ${p.name_en}`} onSave={(n) => n > 0 && save(`/api/products/${p.id}`, { price: n }, 'the price')} />
                </td>
                <td className="px-5 py-3">
                  <StockGrid p={p} onSave={(size, colour, stock) => save(`/api/products/${p.id}/stock`, { size, colour, stock }, 'stock')} />
                </td>
                <td className="px-5 py-3 text-right">
                  <button onClick={() => setForm(p)} className="text-sm text-zinc-600 hover:text-zinc-900 rounded-lg px-2 py-1 hover:bg-zinc-100">Edit</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  )
}

const list = (s) => s.split(',').map((x) => x.trim()).filter(Boolean)

// Add a product (product = null) or edit one: name, category, price, sizes, colours, other names.
function ProductForm({ product, categories, onSaved, onClose, onDelete }) {
  const [f, setF] = useState(product
    ? { name_en: product.name_en, name_bn: product.name_bn, category: product.category, price: String(product.price_bdt),
        sizes: product.sizes.join(', '), colours: product.colours.join(', '), aliases: product.aliases.join(', ') }
    : { name_en: '', name_bn: '', category: '', price: '', stock: '', sizes: '', colours: '', aliases: '' })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => { setF({ ...f, [k]: e.target.value }); setError('') }
  const submit = async (e) => {
    e.preventDefault()
    const body = { ...f, price: Number(f.price), sizes: list(f.sizes), colours: list(f.colours), aliases: list(f.aliases) }
    if (!product) body.stock = Number(f.stock)
    if (!body.sizes.length || !body.colours.length) return setError('Add at least one size and one colour.')
    setSaving(true)
    try {
      const saved = await api(product ? `/api/products/${product.id}` : '/api/products',
        { method: product ? 'PATCH' : 'POST', body: JSON.stringify(body) })
      onSaved(saved, !product)
    } catch (err) {
      setError(err.status === 422 ? 'Check the fields: price must be above 0 and stock 0 or more.' : 'Could not save the product. Try again.')
    }
    setSaving(false)
  }
  const field = (k, label, props = {}) => (
    <label className="block text-sm">
      <span className="font-medium text-zinc-700">{label}</span>
      <input value={f[k]} onChange={set(k)} {...props}
        className="mt-1 w-full rounded-xl px-3 py-2 bg-zinc-50 ring-1 ring-zinc-200 focus:ring-2 focus:ring-emerald-500 focus:bg-white outline-none" />
    </label>
  )
  return (
    <Card className="mb-4 enter">
      <h2 className="text-sm font-semibold text-zinc-900 mb-4">{product ? `Edit ${product.id}` : 'New product'}</h2>
      <form onSubmit={submit} className="grid sm:grid-cols-2 gap-4">
        {field('name_en', 'Name (English)', { required: true, placeholder: 'Maroon Silk Panjabi', autoFocus: true })}
        {field('name_bn', 'Name (Bangla)', { placeholder: 'মেরুন সিল্ক পাঞ্জাবি' })}
        {field('category', 'Category', { required: true, list: 'categories', placeholder: 'panjabi' })}
        <datalist id="categories">{categories.map((c) => <option key={c} value={c} />)}</datalist>
        <div className="grid grid-cols-2 gap-4">
          {field('price', 'Price (৳)', { required: true, type: 'number', min: 1, inputMode: 'numeric' })}
          {!product && field('stock', 'Stock (each size & colour)', { required: true, type: 'number', min: 0, inputMode: 'numeric' })}
        </div>
        {field('sizes', 'Sizes (comma separated)', { required: true, placeholder: 'M, L, XL' })}
        {field('colours', 'Colours (comma separated)', { required: true, placeholder: 'Maroon, Black' })}
        <div className="sm:col-span-2">
          {field('aliases', 'Other names customers type (optional, comma separated)', { placeholder: 'panjabi, punjabi, silk panjabi, meroon panjabi' })}
          <p className="mt-1 text-xs text-zinc-500">
            Banglish spellings and short names help the assistant find this product.
            {product && ' New sizes or colours start with 0 stock; removed ones lose their stock.'}
          </p>
        </div>
        {error && <p role="alert" className="sm:col-span-2 text-sm text-red-600">{error}</p>}
        <div className="sm:col-span-2 flex gap-2 justify-end">
          {product && <button type="button" onClick={() => onDelete(product)} className="mr-auto rounded-xl px-4 py-2 text-sm text-red-600 hover:bg-red-50">Delete product</button>}
          <button type="button" onClick={onClose} className="rounded-xl px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100">Cancel</button>
          <button disabled={saving} className="bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 text-white rounded-xl px-5 py-2 text-sm font-medium">
            {saving ? 'Saving…' : product ? 'Save changes' : 'Add product'}
          </button>
        </div>
      </form>
    </Card>
  )
}
