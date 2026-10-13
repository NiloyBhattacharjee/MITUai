import { cloneElement, useEffect, useState } from 'react'
import Simulator from './Simulator.jsx'
import { Approvals, Bookings, Chats, Kitchen, Menu, Orders } from './Dashboard.jsx'
import Analytics from './Analytics.jsx'
import { Icon, cx, setCurrency } from './ui.jsx'

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      'X-Session': localStorage.getItem('session') || '',
      ...(opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...opts.headers,
    },
  })
  if (res.status === 401) dispatchEvent(new Event('logout'))
  if (!res.ok) throw Object.assign(new Error(await res.text()), { status: res.status })
  return res.json()
}

// Poll fn every ms while mounted (and right away). Pauses while the tab is hidden, refreshes when it's back.
export function usePoll(fn, ms, deps) {
  useEffect(() => {
    let alive = true
    const tick = () => alive && !document.hidden && Promise.resolve(fn()).catch(() => {})
    tick()
    const id = setInterval(tick, ms)
    addEventListener('visibilitychange', tick)
    return () => { alive = false; clearInterval(id); removeEventListener('visibilitychange', tick) }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
}

// Polled GET whose last response is kept per path, so revisiting a page shows data instantly.
// Returns [data (undefined until first load), refresh, set (for optimistic updates)].
const cache = new Map()
export function useLive(path, ms) {
  const [, rerender] = useState(0)
  const set = (d) => { cache.set(path, d); rerender((n) => n + 1) }
  const refresh = () => path && api(path).then(set)
  usePoll(refresh, ms, [path])
  return [cache.get(path), refresh, set]
}

export const toast = (text) => dispatchEvent(new CustomEvent('toast', { detail: text }))
function Toaster() {
  const [items, setItems] = useState([])
  useEffect(() => {
    const on = (e) => {
      const id = Math.random()
      setItems((xs) => [...xs, { id, text: e.detail }])
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4500)
    }
    addEventListener('toast', on)
    return () => removeEventListener('toast', on)
  }, [])
  return (
    <div className="fixed z-50 bottom-20 inset-x-4 md:inset-x-auto md:bottom-6 md:right-6 flex flex-col items-center md:items-end gap-2" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className="enter rounded-xl bg-ink text-inverse text-sm px-4 py-3 shadow-xl max-w-sm">{t.text}</div>
      ))}
    </div>
  )
}

// Theme: follow the system, or a choice saved on this device. index.html applies it before first paint.
const THEMES = ['system', 'light', 'dark']
function useTheme() {
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('theme') || 'system' } catch { return 'system' } })
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') delete root.dataset.theme
    else root.dataset.theme = theme
    try { theme === 'system' ? localStorage.removeItem('theme') : localStorage.setItem('theme', theme) } catch { /* private mode */ }
  }, [theme])
  return [theme, () => setTheme(THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length])]
}
function ThemeButton({ className }) {
  const [theme, next] = useTheme()
  return (
    <button onClick={next} className={cx('btn btn-ghost px-2!', className)} title={`Theme: ${theme}`} aria-label={`Theme: ${theme}. Switch theme`}>
      <Icon name={theme} />
    </button>
  )
}

const PAGES = [
  { id: 'overview', label: 'Overview', el: <Analytics /> },
  // needs: whether this business uses the page; label: its name, unless /api/business gives another (Packing, Products)
  { id: 'kitchen', label: 'Kitchen', el: <Kitchen />, needs: (s) => s.orders !== false },
  { id: 'orders', label: 'Orders', el: <Orders />, needs: (s) => s.orders !== false },
  { id: 'bookings', label: 'Bookings', el: <Bookings />, needs: (s) => s.tables !== false || s.appointments },
  { id: 'approvals', label: 'Approvals', short: 'Approve', el: <Approvals /> },
  { id: 'chats', label: 'Conversations', short: 'Chats', el: <Chats /> },
  { id: 'menu', label: 'Menu', el: <Menu /> },
]

// Sign in with the business's ID and your own password; the session token goes with every request after that.
function Login({ onDone }) {
  const [shopId, setShopId] = useState(() => localStorage.getItem('shop') || '')
  const [pw, setPw] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const r = await api('/api/login', { method: 'POST', body: JSON.stringify({ shop: shopId, password: pw }) })
      localStorage.setItem('session', r.token)
      localStorage.setItem('shop', r.shop)
      onDone()
    } catch (err) {
      setError(err.status === 401 ? 'Wrong business ID or password. Try again.' : err.status === 429 ? 'Too many wrong passwords. Try again in 15 minutes.' : 'Cannot reach the server.')
    }
    setBusy(false)
  }
  return (
    <div className="min-h-dvh grid lg:grid-cols-[1.1fr_1fr]">
      <div className="hidden lg:flex relative overflow-hidden flex-col justify-between p-12 bg-[#0b1512] text-white">
        <div className="absolute -top-48 -left-32 w-[40rem] h-[40rem] rounded-full bg-emerald-500/20 blur-3xl" />
        <div className="absolute bottom-0 right-0 w-[28rem] h-[28rem] rounded-full bg-teal-300/10 blur-3xl" />
        <Brand light />
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold tracking-tight leading-[1.1]">Your shop's AI assistant: every DM answered, every order taken.</h1>
          <p className="mt-5 text-white/60 leading-relaxed">Orders, stock, bookings and every WhatsApp, Messenger and Instagram chat in one place. Step in whenever a customer needs a person.</p>
        </div>
        <p className="relative text-xs text-white/40">© {new Date().getFullYear()} Mitu</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="enter w-full max-w-sm">
          <div className="lg:hidden mb-12"><Brand /></div>
          <h2 className="text-2xl font-semibold tracking-tight">Welcome back</h2>
          <p className="mt-1 text-sm text-muted">Sign in to your business's dashboard.</p>
          <label htmlFor="shop" className="block mt-8 mb-2 text-sm font-medium">Business ID</label>
          <input id="shop" value={shopId} onChange={(e) => { setShopId(e.target.value); setError('') }} autoFocus={!shopId} required
            autoCapitalize="none" autoCorrect="off" spellCheck="false" placeholder="your-business" className="field h-11!" />
          <label htmlFor="pw" className="block mt-5 mb-2 text-sm font-medium">Your password</label>
          <input id="pw" type="password" value={pw} onChange={(e) => { setPw(e.target.value); setError('') }} autoFocus={!!shopId} required className="field h-11!" />
          {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
          <button disabled={busy} className="btn btn-primary w-full h-11! mt-5">{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </div>
    </div>
  )
}

function Brand({ light }) {
  return (
    <div className="relative flex items-center gap-2.5">
      <div className="w-8 h-8 rounded-[10px] bg-linear-to-br from-emerald-400 to-teal-600 grid place-items-center text-white font-semibold text-sm shadow-sm shadow-emerald-600/30">M</div>
      <span className={cx('font-semibold tracking-tight', light ? 'text-white' : 'text-ink')}>Mitu</span>
    </div>
  )
}

function Shell({ page, shop }) {
  const [convs] = useLive('/api/conversations', 5000)
  const [me] = useLive('/api/me', 60000)
  const [asks] = useLive('/api/approvals', 5000)
  const waiting = convs?.filter((c) => c.needs_human).length
  const counts = { chats: waiting, approvals: asks?.filter((a) => a.status === 'pending').length }
  const pages = PAGES.filter((p) => !p.needs || p.needs(shop)).map((p) => ({ ...p, label: shop.labels?.[p.id] || p.label }))
  const current = pages.find((p) => p.id === page) || pages[0]
  const logout = () => { api('/api/logout', { method: 'POST' }).catch(() => {}).finally(() => dispatchEvent(new Event('logout'))) }
  const badge = (p) => counts[p.id] > 0 && (
    <span className="rounded-full bg-warn px-1.5 min-w-5 text-center text-[11px] font-semibold leading-5 text-surface"
      title={p.id === 'chats' ? `${waiting} waiting for a person` : `${counts[p.id]} to decide`}>{counts[p.id]}</span>
  )
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[240px_1fr]">
      <aside className="hidden md:flex sticky top-0 h-dvh flex-col border-r border-line bg-surface px-3 py-5">
        <div className="px-3 mb-8"><Brand /></div>
        <nav className="flex-1 space-y-0.5">
          {pages.map((p) => {
            const active = current.id === p.id
            return (
              <a key={p.id} href={`#/${p.id}`} aria-current={active ? 'page' : undefined}
                className={cx('flex items-center gap-3 h-9 px-3 rounded-lg text-sm transition',
                  active ? 'bg-subtle text-ink font-medium' : 'text-muted hover:text-ink hover:bg-subtle/60')}>
                <span className={active ? 'text-brand' : ''}><Icon name={p.id} /></span>
                {p.label}
                <span className="ml-auto">{badge(p)}</span>
              </a>
            )
          })}
        </nav>
        <a href="#/simulator" className="flex items-center gap-3 h-9 px-3 rounded-lg text-sm text-muted hover:text-ink hover:bg-subtle/60">
          <Icon name="simulator" /> Chat simulator
        </a>
        <div className="mt-4 pt-4 border-t border-line flex items-center gap-2.5 px-2">
          <div className="w-8 h-8 shrink-0 rounded-full bg-brand-soft text-brand grid place-items-center text-xs font-semibold">{(me?.name || shop.name)[0]}</div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium truncate">{me?.name ?? '…'}</div>
            <div className="text-xs text-faint truncate">{shop.name}</div>
          </div>
          <ThemeButton />
          <button onClick={logout} title="Sign out" aria-label="Sign out" className="btn btn-ghost px-2!"><Icon name="logout" /></button>
        </div>
      </aside>

      <header className="md:hidden sticky top-0 z-20 flex items-center justify-between px-4 h-14 border-b border-line bg-canvas/80 backdrop-blur-lg">
        <Brand />
        <div className="flex items-center">
          <a href="#/simulator" className="btn btn-ghost px-2!" aria-label="Chat simulator"><Icon name="simulator" /></a>
          <ThemeButton />
          <button onClick={logout} aria-label="Sign out" className="btn btn-ghost px-2!"><Icon name="logout" /></button>
        </div>
      </header>

      <main className="min-w-0 px-4 pt-6 pb-28 md:px-10 md:pt-10 md:pb-12">
        <div key={current.id} className="enter max-w-6xl mx-auto">{cloneElement(current.el, { title: current.label })}</div>
      </main>

      <nav style={{ gridTemplateColumns: `repeat(${pages.length}, minmax(0, 1fr))` }}
        className="md:hidden fixed bottom-0 inset-x-0 z-20 grid border-t border-line bg-surface/90 backdrop-blur-lg pb-[env(safe-area-inset-bottom)]">
        {pages.map((p) => {
          const active = current.id === p.id
          return (
            <a key={p.id} href={`#/${p.id}`} aria-current={active ? 'page' : undefined}
              className={cx('relative flex flex-col items-center gap-1 pt-2.5 pb-2 text-[11px]', active ? 'text-ink font-medium' : 'text-faint')}>
              <span className={active ? 'text-brand' : ''}><Icon name={p.id} className="w-5 h-5" /></span>
              {p.short || p.label}
              {counts[p.id] > 0 && <span className="absolute top-1.5 left-1/2 ml-2 w-2 h-2 rounded-full bg-warn" />}
            </a>
          )
        })}
      </nav>
      <Toaster />
    </div>
  )
}

export default function App() {
  const [hash, setHash] = useState(location.hash)
  const [authed, setAuthed] = useState(!!localStorage.getItem('session'))
  const [shop, setShop] = useState({ name: 'Mitu' })
  useEffect(() => {
    const onHash = () => setHash(location.hash)
    const onLogout = () => { localStorage.removeItem('session'); cache.clear(); setAuthed(false) }
    addEventListener('hashchange', onHash)
    addEventListener('logout', onLogout)
    return () => { removeEventListener('hashchange', onHash); removeEventListener('logout', onLogout) }
  }, [])
  useEffect(() => {
    if (authed) api('/api/business').then((b) => { setCurrency(b.currency); setShop(b) }).catch(() => {})
  }, [authed])
  if (!authed) return <Login onDone={() => setAuthed(true)} />
  if (hash === '#/simulator') return (
    <div className="min-h-dvh">
      <nav className="sticky top-0 z-20 flex items-center justify-between px-4 md:px-8 h-14 border-b border-line bg-canvas/80 backdrop-blur-lg">
        <Brand />
        <div className="flex items-center gap-1">
          <ThemeButton />
          <a href="#/" className="btn btn-ghost">← Dashboard</a>
        </div>
      </nav>
      <Simulator />
    </div>
  )
  return <Shell page={hash.replace('#/', '')} shop={shop} />
}
