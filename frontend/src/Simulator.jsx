import { useEffect, useRef, useState } from 'react'
import { api, usePoll } from './App.jsx'
import { Icon, cx } from './ui.jsx'

const EXAMPLES = [
  { lang: 'Bangla', topic: 'Product & price', text: 'ভাই, কালো পাঞ্জাবি আছে? দাম কত?' },
  { lang: 'Banglish', topic: 'Delivery charge', text: 'apu delivery charge koto outside dhaka?' },
  { lang: 'English', topic: 'Place an order', text: 'I want to order the blue kurti in M size' },
  { lang: 'Banglish', topic: 'Order status', text: 'amar order kothay? 01712345678' },
  { lang: 'English', topic: 'Human handoff', text: 'I want to talk to a real person' },
]
const LANG_STYLE = { Bangla: 'bg-ok/10 text-ok', Banglish: 'bg-violet/10 text-violet', English: 'bg-info/10 text-info' }

export const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// wa: WhatsApp colours for the simulator's phone; otherwise the dashboard's theme colours.
export function Bubble({ m, mine, wa }) {
  const look = wa
    ? (mine ? 'bg-[#d9fdd3] text-zinc-900 rounded-tr-sm shadow-sm' : 'bg-white text-zinc-900 rounded-tl-sm shadow-sm')
    : !mine ? 'bg-surface border border-line rounded-bl-md'
      : m.role === 'human' ? 'bg-warn/10 rounded-br-md' : 'bg-brand-soft rounded-br-md'
  return (
    <div className={cx('enter flex', mine ? 'justify-end' : 'justify-start')}>
      <div className={cx('max-w-[80%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap break-words transition-opacity', m.pending && 'opacity-60', look)}>
        {m.role === 'human' && <div className={cx('text-[11px] font-semibold mb-0.5', wa ? 'text-amber-700' : 'text-warn')}>Shop team{m.author && ` · ${m.author}`}</div>}
        {m.audio_url && <audio controls src={m.audio_url} className="h-9 max-w-full my-1" />}
        {m.image_url && <img src={m.image_url} alt={m.text} className="rounded-xl max-h-72 my-1" />}
        {m.is_voice
          ? <div className={cx('flex gap-1.5 text-xs italic', wa ? 'text-zinc-500' : 'text-muted')}><Icon name="mic" className="w-3.5 h-3.5 shrink-0 mt-0.5" />{m.text || '(no transcript)'}</div>
          : m.text}
        <div className={cx('text-[10px] text-right mt-1 tabular-nums', wa ? 'text-zinc-400' : 'text-faint')}>{time(m.created_at)}</div>
      </div>
    </div>
  )
}

const Typing = () => (
  <div className="enter flex justify-start" aria-label="Assistant is typing">
    <div className="flex gap-1 rounded-xl rounded-tl-sm bg-white px-3 py-2.5 shadow-sm">
      {[0, 150, 300].map((d) => <span key={d} className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" style={{ animationDelay: `${d}ms` }} />)}
    </div>
  </div>
)

export default function Simulator() {
  const [phone, setPhone] = useState('')
  const [started, setStarted] = useState(null)
  const [data, setData] = useState({ messages: [], bot_enabled: true })
  const [text, setText] = useState('')
  const [status, setStatus] = useState('')
  const [recording, setRecording] = useState(false)
  const [shop, setShop] = useState({ name: 'Shop', agent_name: 'Mitu' })
  const recorder = useRef(null)
  const holding = useRef(false)
  const bottom = useRef(null)

  useEffect(() => { api('/api/business').then(setShop).catch(() => {}) }, [])
  const refresh = () => started && api(`/api/sim/${encodeURIComponent(started)}/messages`).then((d) => {
    setData((old) => {
      if (d.messages.length !== old.messages.length) setStatus('')
      return d
    })
  })
  usePoll(refresh, 1000, [started])

  const last = data.messages.at(-1)
  const typing = last?.role === 'user' && data.bot_enabled
  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }), [data.messages.length, typing, status])

  const send = async (t) => {
    if (!t.trim() || !started) return
    setText('')
    await api('/api/sim/message', { method: 'POST', body: JSON.stringify({ phone: started, text: t }) })
    setTimeout(refresh, 300)
  }

  const startRec = async (e) => {
    e.preventDefault()
    holding.current = true
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      const chunks = []
      rec.ondataavailable = (ev) => chunks.push(ev.data)
      rec.onstop = async () => {
        setRecording(false)
        stream.getTracks().forEach((tr) => tr.stop())
        const blob = new Blob(chunks, { type: rec.mimeType })
        if (blob.size < 1000) return setStatus('Too short. Hold the mic button while you speak.')
        const form = new FormData()
        form.append('phone', started)
        form.append('file', blob, 'voice')
        setStatus('Sending voice note…')
        await api('/api/sim/voice', { method: 'POST', body: form })
      }
      rec.start()
      recorder.current = rec
      setRecording(true)
      setStatus('Recording… release to send')
      if (!holding.current) stopRec() // released before the mic was ready
    } catch {
      setStatus('Microphone not available')
    }
  }
  const stopRec = () => {
    holding.current = false
    if (recorder.current?.state === 'recording') recorder.current.stop()
    recorder.current = null
  }
  const restart = () => { setStarted(null); setData({ messages: [], bot_enabled: true }); setStatus('') }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 md:py-14 grid lg:grid-cols-[1fr_400px] gap-10 lg:gap-16 items-start">
      <section className="enter">
        <span className="chip bg-brand-soft text-brand px-3! py-1!">
          <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse" /> Live demo
        </span>
        <h1 className="mt-5 text-3xl md:text-[2.5rem] md:leading-[1.1] font-semibold tracking-tight">
          Chat with {shop.agent_name}, {shop.name}'s AI assistant
        </h1>
        <p className="mt-4 text-muted max-w-xl leading-relaxed">
          Message in Bangla, English or Banglish, or hold the mic to send a voice note. This runs the exact same agent as real WhatsApp messages, and every chat shows up live in the dashboard.
        </p>

        <h2 className="mt-12 mb-4 text-sm font-medium">Try a message</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          {EXAMPLES.map((ex) => (
            <button key={ex.text} disabled={!started} onClick={() => send(ex.text)}
              className="card text-left p-4 transition enabled:hover:border-brand/50 enabled:hover:-translate-y-0.5 enabled:active:translate-y-0 disabled:opacity-50 disabled:cursor-not-allowed">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted">{ex.topic}</span>
                <span className={cx('chip text-[10px]!', LANG_STYLE[ex.lang])}>{ex.lang}</span>
              </div>
              <div className="mt-2.5 text-sm">{ex.text}</div>
            </button>
          ))}
        </div>
        {!started && <p className="mt-4 text-xs text-faint">Start a chat on the phone first.</p>}
      </section>

      <div className="enter order-first lg:order-none mx-auto w-full max-w-[400px] rounded-[3rem] bg-zinc-900 p-3 shadow-2xl shadow-zinc-950/20 ring-1 ring-zinc-950/10">
        <div className="relative h-[min(760px,80vh)] rounded-[2.4rem] overflow-hidden bg-[#efeae2] text-zinc-900 [color-scheme:light] flex flex-col">
          <div className="absolute top-2 left-1/2 -translate-x-1/2 w-24 h-6 rounded-full bg-zinc-900 z-10" aria-hidden="true" />
          <header className="bg-[#008069] text-white pt-10 pb-3 px-4 flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-white/20 grid place-items-center font-semibold">{shop.name[0]}</div>
            <div className="min-w-0 flex-1">
              <div className="font-semibold leading-tight truncate">{shop.name}</div>
              <div className="text-xs text-white/80">{typing ? 'typing…' : started ? 'online' : 'WhatsApp Business'}</div>
            </div>
            {started && <button onClick={restart} className="text-xs text-white/80 hover:text-white rounded-md px-2 py-1 hover:bg-white/10">New chat</button>}
          </header>

          {!started ? (
            <form className="flex-1 grid place-items-center p-6"
              onSubmit={(e) => { e.preventDefault(); phone.trim() && setStarted(phone.trim()) }}>
              <div className="w-full bg-white rounded-2xl p-6 shadow-sm">
                <h2 className="font-semibold text-zinc-900">Start a chat</h2>
                <p className="mt-1 text-sm text-zinc-500">Pick any phone number. It identifies this customer in the dashboard.</p>
                <label htmlFor="sim-phone" className="block mt-5 text-sm font-medium text-zinc-700">Phone number</label>
                <input id="sim-phone" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="01712345678" inputMode="tel"
                  className="mt-2 w-full rounded-xl px-4 py-2.5 bg-zinc-50 ring-1 ring-zinc-200 focus:ring-2 focus:ring-emerald-500 focus:bg-white outline-none transition" />
                <button className="mt-4 w-full rounded-xl py-2.5 bg-[#008069] hover:bg-[#006e5a] text-white font-medium transition">Start chat</button>
              </div>
            </form>
          ) : (
            <>
              <div className="flex-1 overflow-y-auto px-3 py-4 space-y-2">
                <div className="mx-auto w-fit max-w-[85%] text-center rounded-lg bg-[#fff5c4] text-[11px] text-zinc-600 px-3 py-1.5 shadow-sm">
                  Chatting as {started}. Replies come from an AI assistant.
                </div>
                {data.messages.map((m) => <Bubble key={m.id} m={m} mine={m.role === 'user'} wa />)}
                {typing && <Typing />}
                {status && <div className="mx-auto w-fit rounded-full bg-white/80 px-3 py-1 text-xs text-zinc-600 shadow-sm">{status}</div>}
                <div ref={bottom} />
              </div>
              <form className="flex items-center gap-2 p-2 pb-4 bg-[#f0f2f5]" onSubmit={(e) => { e.preventDefault(); send(text) }}>
                <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" aria-label="Message"
                  className="flex-1 min-w-0 rounded-full px-4 py-2.5 bg-white outline-none text-sm text-zinc-900 placeholder:text-zinc-400" />
                {text
                  ? <button className="w-11 h-11 shrink-0 rounded-full bg-[#008069] hover:bg-[#006e5a] text-white grid place-items-center transition" aria-label="Send">
                      <Icon name="send" />
                    </button>
                  : <button type="button" aria-label="Hold to record a voice note" title="Hold to record"
                      onPointerDown={startRec} onPointerUp={stopRec} onPointerLeave={stopRec} onContextMenu={(e) => e.preventDefault()}
                      className={`w-11 h-11 shrink-0 rounded-full text-white grid place-items-center select-none touch-none transition ${recording ? 'bg-red-500 scale-110 ring-4 ring-red-500/25' : 'bg-[#008069] hover:bg-[#006e5a]'}`}>
                      <Icon name="mic" />
                    </button>}
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
