// Public landing page: what Mitu does, with the three example businesses playing out as live chats.
// Shown to anyone signed out; "Sign in" goes to #/login. Demo data comes from business.yaml and examples/*.yaml.
import { useEffect, useRef, useState } from 'react'
import { Icon, cx } from './ui.jsx'

const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches

// Adds .in to the element the first time it scrolls into view (the CSS does the rest).
function useReveal() {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { el.classList.add('in'); io.disconnect() } }, { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return ref
}
const Reveal = ({ as: Tag = 'div', className, delay = 0, children }) => (
  <Tag ref={useReveal()} className={cx('reveal', className)} style={{ transitionDelay: `${delay}ms` }}>{children}</Tag>
)

// Counts from 0 to `to` once visible.
function CountUp({ to, suffix = '' }) {
  const ref = useRef(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    if (still()) return setN(to)
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return
      io.disconnect()
      const t0 = performance.now()
      const step = (t) => { const p = Math.min(1, (t - t0) / 1200); setN(Math.round(to * (1 - (1 - p) ** 3))); if (p < 1) requestAnimationFrame(step) }
      requestAnimationFrame(step)
    })
    io.observe(ref.current)
    return () => io.disconnect()
  }, [to])
  return <span ref={ref}>{n}{suffix}</span>
}

// Plays a script of chat lines one by one, with a typing indicator before each of Mitu's replies, then loops.
// Each line is [who, text, effect?]; effect names the dashboard card that lights up when that line lands.
function useScript(lines, key) {
  const [shown, setShown] = useState(still() ? lines.length : 0)
  const [typing, setTyping] = useState(false)
  useEffect(() => {
    if (still()) return setShown(lines.length)
    let i = 0, timer
    setShown(0)
    const next = () => {
      if (i >= lines.length) { timer = setTimeout(() => { i = 0; setShown(0); next() }, 4500); return }
      const mine = lines[i][0] === 'mitu'
      setTyping(mine)
      timer = setTimeout(() => { setTyping(false); setShown(++i); next() }, mine ? 1400 : 900)
    }
    timer = setTimeout(next, 500)
    return () => clearTimeout(timer)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return [lines.slice(0, shown), typing]
}

function Phone({ title, sub, lines, typing, className }) {
  const box = useRef(null)
  useEffect(() => { box.current.scrollTop = box.current.scrollHeight }, [lines.length, typing])
  return (
    <div className={cx('relative w-full max-w-[320px] rounded-[2.5rem] border-[10px] border-ink/90 bg-surface shadow-2xl shadow-black/20 overflow-hidden', className)}>
      <div className="flex items-center gap-2.5 px-4 py-3 bg-[#075e54] text-white">
        <div className="w-8 h-8 rounded-full bg-white/20 grid place-items-center text-sm font-semibold">{title[0]}</div>
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{title}</div>
          <div className="text-[11px] text-white/70">{typing ? 'typing…' : sub}</div>
        </div>
      </div>
      <div ref={box} className="h-[360px] overflow-y-auto scroll-smooth px-3 py-3 space-y-2 bg-subtle text-[13px] leading-snug [scrollbar-width:none]">
        {lines.map(([who, text], i) => (
          <div key={i} className={cx('bubble max-w-[85%] w-fit rounded-xl px-3 py-2 shadow-sm',
            who === 'mitu' ? 'bg-surface rounded-tl-sm' : 'ml-auto bg-brand-soft rounded-tr-sm')}>
            {text}
          </div>
        ))}
        {typing && (
          <div className="bubble w-fit rounded-xl rounded-tl-sm bg-surface px-3 py-3 flex gap-1">
            {[0, 1, 2].map((d) => <span key={d} className="dot w-1.5 h-1.5 rounded-full bg-[#999]" style={{ animationDelay: `${d * 150}ms` }} />)}
          </div>
        )}
      </div>
    </div>
  )
}

const HERO = [
  ['them', 'price?'],
  ['mitu', 'Indigo Block-print Kurti is ৳1,450. Sizes S to XL, 12 left 💙'],
  ['them', 'L available? COD outside Dhaka?'],
  ['mitu', 'Yes, L is in stock. Delivery outside Dhaka is ৳130, cash on delivery is fine. Total ৳1,580. Shall I place it?'],
  ['them', 'yes'],
  ['mitu', 'Done ✓ Order #1042 is placed. We’ll message you when it ships.'],
]

const DEMOS = [
  {
    id: 'shop', label: 'Clothing shop', name: 'Nakshi Threads', where: 'Dhaka · Facebook, Instagram, WhatsApp',
    blurb: 'Sells kurtis, sarees and panjabis in DMs. Stock counted down, delivery zones, cash on delivery or bKash.',
    lines: [
      ['them', 'how much is this one? 📷 [screenshot of a post]'],
      ['mitu', 'That’s the Mustard Embroidered Kurti, ৳1,650. Only 3 left! Which size?'],
      ['them', 'M. delivery charge outside dhaka?'],
      ['mitu', '৳130 by courier, 2–4 days. Cash on delivery or bKash. Total ৳1,780. Confirm?'],
      ['them', 'bkash. confirm', 'order'],
      ['mitu', 'Order #1043 placed ✓ Send ৳1,780 to 01700-000000 and reply with the transaction ID.', 'stock'],
    ],
    cards: {
      order: { icon: 'orders', title: 'New order #1043', lines: ['Mustard Embroidered Kurti · M', 'Outside Dhaka · bKash', '৳1,780'], tone: 'info' },
      stock: { icon: 'products', title: 'Stock updated', lines: ['Mustard Embroidered Kurti', '3 → 2 left'], tone: 'warn' },
    },
  },
  {
    id: 'cafe', label: 'Café', name: 'Little Fern Café', where: 'Newtown, Sydney · WhatsApp',
    blurb: 'Coffee, breakfast and dinner Thursday to Saturday. Pickup orders with required options, allergens, voice notes.',
    lines: [
      ['them', '🎤 Voice note · 0:04'],
      ['mitu', 'Heard: “two flat whites, one with oat, for pickup.” Regular size? That’s $10.80, ready in 15 min.'],
      ['them', 'yes please!', 'order'],
      ['mitu', 'Order #87 confirmed ✓ Two flat whites, one oat, ready at the counter in 15 minutes.', 'ready'],
      ['them', 'is the risotto gluten free?'],
      ['mitu', 'The Mushroom Risotto has no gluten ingredients, but our kitchen handles flour, so I can’t promise it’s safe for coeliacs.'],
    ],
    cards: {
      order: { icon: 'kitchen', title: 'Kitchen · New', lines: ['2 × Flat White (1 oat)', 'Pickup · 15 min', '$10.80'], tone: 'info' },
      ready: { icon: 'check', title: 'Customer told', lines: ['Order #87 · Pickup', 'Ready 15 min after ordering'], tone: 'ok' },
    },
  },
]

const FEATURES = [
  ['chats', 'Answers every DM', 'Prices, sizes, colours, what’s in stock, delivery charges, opening hours. Day and night, in seconds.'],
  ['orders', 'Takes orders end to end', 'Pickup or delivery, cash on delivery or bKash, today or a later day. Nothing is placed until the customer says yes.'],
  ['products', 'Never oversells', 'Stock counts down with every order and goes back up when one is cancelled. The last one is never sold twice.'],
  ['mic', 'Voice notes and photos', 'Customers can talk instead of type, or send a screenshot of a post: “how much is this one?”'],
  ['users', 'Hands off to a person', 'Complaints, refunds, odd requests or “can I talk to someone?” go to your team, and the AI steps back in that chat.'],
  ['kitchen', 'Live packing board', 'Orders land in New, Preparing and Ready columns with a sound. Pause online orders with one button.'],
  ['overview', 'Know your numbers', 'Revenue, top items, pickup vs delivery, how much the AI handled and what it cost, against last period.'],
]

const TONE = { info: 'text-info bg-info/10', ok: 'text-ok bg-ok/10', warn: 'text-warn bg-warn/10' }

function Demo() {
  const [tab, setTab] = useState(0)
  const d = DEMOS[tab]
  const [lines, typing] = useScript(d.lines, d.id)
  const lit = new Set(lines.map((l) => l[2]).filter(Boolean))
  return (
    <div>
      <div role="tablist" className="mx-auto flex w-fit gap-1 rounded-2xl border border-line bg-surface p-1">
        {DEMOS.map((x, i) => (
          <button key={x.id} role="tab" aria-selected={i === tab} onClick={() => setTab(i)}
            className={cx('rounded-xl px-4 h-9 text-sm font-medium transition', i === tab ? 'bg-ink text-inverse' : 'text-muted hover:text-ink')}>
            {x.label}
          </button>
        ))}
      </div>
      <div key={d.id} className="enter mt-10 grid items-center gap-10 lg:grid-cols-[1fr_auto_1fr]">
        <div className="text-center lg:text-left">
          <p className="text-xs font-medium uppercase tracking-widest text-brand">{d.where}</p>
          <h3 className="mt-2 text-2xl font-semibold tracking-tight">{d.name}</h3>
          <p className="mt-3 text-muted leading-relaxed">{d.blurb}</p>
        </div>
        <Phone title={d.name} sub="online" lines={lines} typing={typing} className="mx-auto" />
        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-widest text-faint text-center lg:text-left">Meanwhile, in your dashboard</p>
          {Object.entries(d.cards).map(([k, c]) => (
            <div key={k} className={cx('card p-4 flex gap-3 transition-all duration-500', lit.has(k) ? 'opacity-100 translate-x-0 shadow-lg' : 'opacity-25 translate-x-4')}>
              <span className={cx('w-9 h-9 shrink-0 rounded-xl grid place-items-center', TONE[c.tone])}><Icon name={c.icon} /></span>
              <div className="min-w-0">
                <div className="text-sm font-medium">{c.title}</div>
                {c.lines.map((l) => <div key={l} className="text-sm text-muted">{l}</div>)}
              </div>
              {lit.has(k) && <span className="ml-auto self-start chip bg-brand-soft text-brand pop">live</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const ASKS = ['price?', 'is M available?', 'delivery charge outside Dhaka?', 'COD?', 'how much is this one? 📷',
  'any oat milk?', 'when will my order arrive?', 'is it gluten free?', '🎤 voice note', 'bKash number?']

const Brand = () => (
  <a href="#top" className="flex items-center gap-2.5">
    <span className="w-8 h-8 rounded-[10px] bg-linear-to-br from-emerald-400 to-teal-600 grid place-items-center text-white font-semibold text-sm shadow-sm shadow-emerald-600/30">M</span>
    <span className="font-semibold tracking-tight">Mitu</span>
  </a>
)

export default function Landing() {
  const [lines, typing] = useScript(HERO, 'hero')
  return (
    <div id="top" className="min-h-dvh overflow-x-clip">
      <nav className="sticky top-0 z-30 border-b border-line/60 bg-canvas/75 backdrop-blur-lg">
        <div className="mx-auto max-w-6xl flex items-center justify-between h-16 px-4 md:px-8">
          <Brand />
          <div className="flex items-center gap-1">
            <a href="#features" className="hidden sm:inline-flex btn btn-ghost">Features</a>
            <a href="#demos" className="hidden sm:inline-flex btn btn-ghost">Demos</a>
            <a href="#how" className="hidden sm:inline-flex btn btn-ghost">How it works</a>
            <a href="#/login" className="btn btn-primary ml-2">Sign in</a>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <header className="relative">
        <div className="blob absolute -top-40 -left-40 w-[36rem] h-[36rem] rounded-full bg-emerald-400/25 blur-3xl" />
        <div className="blob absolute top-40 -right-40 w-[30rem] h-[30rem] rounded-full bg-teal-300/20 blur-3xl" style={{ animationDelay: '-6s' }} />
        <div className="relative mx-auto max-w-6xl grid gap-14 px-4 md:px-8 pt-16 pb-20 lg:grid-cols-[1.15fr_1fr] lg:items-center lg:pt-24">
          <div>
            <p className="rise chip bg-brand-soft text-brand px-3! py-1!"><span className="w-1.5 h-1.5 rounded-full bg-brand pulse" /> Answering DMs right now</p>
            <h1 className="rise mt-6 text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight leading-[1.05]" style={{ animationDelay: '80ms' }}>
              Every DM answered.<br /><span className="shimmer bg-clip-text text-transparent">Every order taken.</span>
            </h1>
            <p className="rise mt-6 max-w-xl text-lg text-muted leading-relaxed" style={{ animationDelay: '160ms' }}>
              Mitu is an AI assistant for shops that sell on Facebook, Instagram and WhatsApp. It answers “price?”,
              takes the order, counts the stock and hands off to you when a customer needs a person.
            </p>
            <div className="rise mt-8 flex flex-wrap gap-3" style={{ animationDelay: '240ms' }}>
              <a href="#demos" className="btn btn-primary h-11! px-5!">See it in action</a>
              <a href="#features" className="btn btn-secondary h-11! px-5!">What it does</a>
            </div>
            <div className="rise mt-10 flex flex-wrap gap-2 text-sm text-muted" style={{ animationDelay: '320ms' }}>
              {['WhatsApp', 'Messenger', 'Instagram'].map((c) => <span key={c} className="chip border border-line bg-surface px-3! py-1!">{c}</span>)}
            </div>
          </div>
          <div className="relative mx-auto w-full max-w-[320px]">
            <Phone title="Nakshi Threads" sub="online" lines={lines} typing={typing} className="float" />
            <div className="float-card absolute -left-10 top-16 hidden sm:flex card px-3 py-2 items-center gap-2 shadow-xl text-sm">
              <span className="text-ok"><Icon name="check" /></span> Order #1042 · ৳1,580
            </div>
            <div className="float-card absolute -right-8 bottom-20 hidden sm:flex card px-3 py-2 items-center gap-2 shadow-xl text-sm" style={{ animationDelay: '-3s' }}>
              <span className="text-brand"><Icon name="bot" /></span> Replied in 2 s
            </div>
          </div>
        </div>
      </header>

      {/* What customers ask, scrolling */}
      <div className="border-y border-line bg-surface py-5 overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_10%,#000_90%,transparent)]">
        <div className="marquee flex w-max gap-3">
          {[...ASKS, ...ASKS].map((q, i) => (
            <span key={i} className="rounded-full bg-subtle px-4 py-2 text-sm text-muted whitespace-nowrap">{q}</span>
          ))}
        </div>
      </div>

      {/* Features */}
      <section id="features" className="mx-auto max-w-6xl px-4 md:px-8 py-24 scroll-mt-16">
        <Reveal className="max-w-2xl">
          <p className="text-sm font-medium text-brand">Features</p>
          <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">Your best salesperson, in every chat at once</h2>
          <p className="mt-4 text-muted leading-relaxed">Most messages are the same few questions. Mitu answers them from your own catalogue and settings, and your team only sees what needs them.</p>
        </Reveal>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(([icon, title, text], i) => (
            <Reveal key={title} delay={(i % 3) * 90} className="feature card p-6">
              <span className="w-10 h-10 rounded-xl bg-brand-soft text-brand grid place-items-center"><Icon name={icon} className="w-5 h-5" /></span>
              <h3 className="mt-5 font-medium">{title}</h3>
              <p className="mt-2 text-sm text-muted leading-relaxed">{text}</p>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Demos */}
      <section id="demos" className="relative py-24 bg-surface border-y border-line scroll-mt-16">
        <div className="mx-auto max-w-6xl px-4 md:px-8">
          <Reveal className="text-center max-w-2xl mx-auto mb-10">
            <p className="text-sm font-medium text-brand">Business demos</p>
            <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">One assistant, any business that sells in chat</h2>
            <p className="mt-4 text-muted leading-relaxed">The same Mitu runs a clothing shop and a café. Pick one and watch a real conversation play out.</p>
          </Reveal>
          <Demo />
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto max-w-6xl px-4 md:px-8 py-24 scroll-mt-16">
        <Reveal className="text-center max-w-2xl mx-auto">
          <p className="text-sm font-medium text-brand">How it works</p>
          <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">Set up once, then it just answers</h2>
        </Reveal>
        <div className="relative mt-14 grid gap-6 md:grid-cols-3">
          <div className="flow hidden md:block absolute top-8 left-[16%] right-[16%] h-0.5" />
          {[
            ['products', 'Describe your business', 'Products, prices, stock, delivery zones, payment methods and hours, in one settings file or the dashboard.'],
            ['send', 'Connect your channels', 'Your WhatsApp number, Facebook Page and Instagram account. Every channel runs the same assistant.'],
            ['overview', 'Watch the orders come in', 'Follow every chat live, step in with one click and see what sold.'],
          ].map(([icon, title, text], i) => (
            <Reveal key={title} delay={i * 150} className="relative text-center">
              <span className="relative mx-auto w-16 h-16 rounded-2xl bg-ink text-inverse grid place-items-center shadow-lg"><Icon name={icon} className="w-6 h-6" />
                <span className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-brand text-inverse text-xs font-semibold grid place-items-center">{i + 1}</span>
              </span>
              <h3 className="mt-6 font-medium">{title}</h3>
              <p className="mt-2 text-sm text-muted leading-relaxed max-w-xs mx-auto">{text}</p>
            </Reveal>
          ))}
        </div>
        <div className="mt-20 grid grid-cols-2 md:grid-cols-4 gap-4">
          {[[3, '', 'channels: WhatsApp, Messenger, Instagram'], [2, '', 'kinds of business, one install'], [10, '', 'tools the AI can use'], [24, '/7', 'answering, even at 3 am']].map(([n, s, label]) => (
            <Reveal key={label} className="card p-6 text-center">
              <div className="text-4xl font-semibold tracking-tight text-brand tabular-nums"><CountUp to={n} suffix={s} /></div>
              <div className="mt-2 text-sm text-muted">{label}</div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="px-4 md:px-8 pb-24">
        <Reveal className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl bg-[#0b1512] px-6 py-16 text-center text-white">
          <div className="blob absolute -top-32 left-1/4 w-[28rem] h-[28rem] rounded-full bg-emerald-500/25 blur-3xl" />
          <div className="relative">
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">Stop answering “price?” at midnight</h2>
            <p className="mt-4 text-white/60 max-w-xl mx-auto">Let Mitu take the DMs. You pack the orders.</p>
            <a href="#/login" className="btn mt-8 h-11! px-6! bg-white text-[#0b1512] hover:bg-white/90">Sign in to your dashboard</a>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-line py-8 px-4 md:px-8">
        <div className="mx-auto max-w-6xl flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-faint">
          <Brand />
          <span>© {new Date().getFullYear()} Mitu</span>
        </div>
      </footer>
    </div>
  )
}
