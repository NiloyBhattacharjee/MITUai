import { useEffect, useState } from 'react'
import Simulator from './Simulator.jsx'
import { Chats, Orders, Products } from './Dashboard.jsx'
import Analytics from './Analytics.jsx'

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      'X-Admin-Password': localStorage.getItem('adminPassword') || '',
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
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4000)
    }
    addEventListener('toast', on)
    return () => removeEventListener('toast', on)
  }, [])
  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className="enter rounded-xl bg-zinc-900 text-white text-sm px-4 py-3 shadow-xl max-w-sm">{t.text}</div>
      ))}
    </div>
  )
}

const ICONS = {
  overview: 'M3 3v18h18M7 16v-4M12 16V8M17 16V5',
  chats: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  orders: 'M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4zM3 6h18M16 10a4 4 0 0 1-8 0',
  products: 'M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82zM7 7h.01',
  simulator: 'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM11 18h2',
  logout: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  mic: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
}
export const Icon = ({ name, className = 'w-5 h-5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d={ICONS[name]} />
  </svg>
)

const PAGES = [
  { id: 'overview', label: 'Overview', el: <Analytics /> },
  { id: 'chats', label: 'Conversations', el: <Chats /> },
  { id: 'orders', label: 'Orders', el: <Orders /> },
  { id: 'products', label: 'Products', el: <Products /> },
]

function Login({ shop, onDone }) {
  const [pw, setPw] = useState('')
  const [error, setError] = useState('')
  const submit = async (e) => {
    e.preventDefault()
    localStorage.setItem('adminPassword', pw)
    try {
      await api('/api/me')
      onDone()
    } catch (err) {
      setError(err.status === 401 ? 'Wrong password. Try again.' : 'Cannot reach the server.')
    }
  }
  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-white">
      <div className="hidden lg:flex flex-col justify-between p-12 bg-zinc-950 text-white relative overflow-hidden">
        <div className="absolute -top-40 -left-40 w-[36rem] h-[36rem] rounded-full bg-emerald-500/25 blur-3xl" />
        <div className="absolute -bottom-48 right-0 w-[30rem] h-[30rem] rounded-full bg-teal-400/15 blur-3xl" />
        <Brand />
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold tracking-tight leading-tight">Your shop's AI assistant, and every sale it makes.</h1>
          <p className="mt-4 text-zinc-400">Watch revenue, orders and WhatsApp conversations in one place. Step in whenever a customer needs a human.</p>
        </div>
        <p className="relative text-xs text-zinc-500">© {new Date().getFullYear()} {shop.name}</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm">
          <div className="lg:hidden mb-10"><Brand dark /></div>
          <h2 className="text-2xl font-semibold tracking-tight text-zinc-900">Sign in</h2>
          <p className="mt-1 text-sm text-zinc-500">to the {shop.name} dashboard</p>
          <label htmlFor="pw" className="block mt-8 text-sm font-medium text-zinc-700">Password</label>
          <input id="pw" type="password" value={pw} onChange={(e) => { setPw(e.target.value); setError('') }} autoFocus required
            className="mt-2 w-full rounded-xl px-4 py-2.5 bg-zinc-50 ring-1 ring-zinc-200 focus:ring-2 focus:ring-emerald-500 focus:bg-white outline-none transition" />
          {error && <p role="alert" className="mt-2 text-sm text-red-600">{error}</p>}
          <button className="mt-6 w-full rounded-xl py-2.5 bg-zinc-900 text-white font-medium hover:bg-zinc-800 active:scale-[.99] transition">
            Sign in
          </button>
          <a href="#/simulator" className="block mt-6 text-center text-sm text-zinc-500 hover:text-zinc-900">Open the chat simulator →</a>
        </form>
      </div>
    </div>
  )
}

function Brand({ dark }) {
  return (
    <div className="relative flex items-center gap-2.5">
      <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-400 to-teal-600 grid place-items-center text-white font-bold text-sm shadow-lg shadow-emerald-500/30">M</div>
      <span className={`font-semibold tracking-tight ${dark ? 'text-zinc-900' : 'text-white'}`}>Mitu</span>
    </div>
  )
}

function Shell({ page, shop }) {
  const [convs] = useLive('/api/conversations', 5000)
  const [me] = useLive('/api/me', 60000)
  const waiting = convs?.filter((c) => c.needs_human).length
  const current = PAGES.find((p) => p.id === page) || PAGES[0]
  const link = (p, mobile) => (
    <a key={p.id} href={`#/${p.id}`} aria-current={current.id === p.id ? 'page' : undefined}
      className={`flex items-center gap-3 rounded-lg text-sm transition ${mobile ? 'px-3 py-1.5 whitespace-nowrap' : 'px-3 py-2'} ${current.id === p.id
        ? 'bg-white/10 text-white' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}>
      {!mobile && <Icon name={p.id} />}
      {p.label}
      {p.id === 'chats' && waiting > 0 && (
        <span className="ml-auto text-[11px] font-semibold rounded-full px-1.5 min-w-5 text-center bg-amber-400 text-zinc-950" title={`${waiting} waiting for a human`}>{waiting}</span>
      )}
    </a>
  )
  const logout = () => dispatchEvent(new Event('logout'))
  return (
    <div className="min-h-screen bg-zinc-100 md:flex">
      <aside className="hidden md:flex w-60 shrink-0 flex-col bg-zinc-950 p-4 sticky top-0 h-screen">
        <div className="px-2 py-2 mb-6"><Brand /></div>
        <nav className="space-y-1 flex-1">{PAGES.map((p) => link(p))}</nav>
        <a href="#/simulator" className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-zinc-400 hover:text-white hover:bg-white/5">
          <Icon name="simulator" /> Chat simulator
        </a>
        <div className="mt-3 pt-3 border-t border-white/10 flex items-center gap-3 px-2">
          <div className="w-8 h-8 rounded-full bg-zinc-800 grid place-items-center text-xs font-semibold text-zinc-300">{shop.name[0]}</div>
          <div className="min-w-0 flex-1">
            <div className="text-sm text-white truncate">{shop.name}</div>
            <div className="text-xs text-zinc-500">{me?.name ?? '…'}</div>
          </div>
          <button onClick={logout} title="Sign out" aria-label="Sign out" className="text-zinc-500 hover:text-white"><Icon name="logout" className="w-4 h-4" /></button>
        </div>
      </aside>
      <header className="md:hidden bg-zinc-950 px-4 pt-3 pb-2 sticky top-0 z-10">
        <div className="flex items-center justify-between mb-2">
          <Brand />
          <button onClick={logout} aria-label="Sign out" className="text-zinc-400"><Icon name="logout" /></button>
        </div>
        <nav className="flex gap-1 overflow-x-auto">{PAGES.map((p) => link(p, true))}</nav>
      </header>
      <main className="flex-1 min-w-0 p-4 md:p-8">
        <div key={current.id} className="enter max-w-7xl mx-auto">{current.el}</div>
      </main>
      <Toaster />
    </div>
  )
}

export default function App() {
  const [hash, setHash] = useState(location.hash)
  const [authed, setAuthed] = useState(!!localStorage.getItem('adminPassword'))
  const [shop, setShop] = useState({ name: 'Shop' })
  useEffect(() => {
    const onHash = () => setHash(location.hash)
    const onLogout = () => { localStorage.removeItem('adminPassword'); cache.clear(); setAuthed(false) }
    addEventListener('hashchange', onHash)
    addEventListener('logout', onLogout)
    api('/api/business').then(setShop).catch(() => {})
    return () => { removeEventListener('hashchange', onHash); removeEventListener('logout', onLogout) }
  }, [])
  if (hash === '#/simulator') return (
    <div className="min-h-screen bg-zinc-100">
      <nav className="bg-zinc-950 px-4 py-3 flex items-center justify-between">
        <Brand />
        <a href="#/" className="text-sm text-zinc-400 hover:text-white">← Dashboard</a>
      </nav>
      <Simulator />
    </div>
  )
  if (!authed) return <Login shop={shop} onDone={() => setAuthed(true)} />
  return <Shell page={hash.replace('#/', '')} shop={shop} />
}
