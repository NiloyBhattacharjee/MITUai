// Public landing page: what Mitu does, with the example businesses playing out as live WhatsApp chats. English or Bangla.
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
function CountUp({ to, suffix = '', locale }) {
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
  return <span ref={ref}>{n.toLocaleString(locale)}{suffix}</span>
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


// A WhatsApp chat as the customer sees it: the shop is the contact, the customer's own messages are on the right.
// Fixed size, so the frame never moves as messages arrive. Colours are WhatsApp's own (.wa in index.css).
function Phone({ title, t, lines, typing, className }) {
  const box = useRef(null)
  useEffect(() => { box.current.scrollTop = box.current.scrollHeight }, [lines.length, typing])
  const time = (i) => new Date(2000, 0, 1, 22, 41 + i).toLocaleTimeString(t.locale, { hour: 'numeric', minute: '2-digit' })
  return (
    <div className={cx('wa relative w-[300px] max-w-full shrink-0 rounded-[2.75rem] border-[10px] border-[#1b1b1d] bg-[#1b1b1d] shadow-2xl shadow-black/25 overflow-hidden', className)}>
      <div className="flex items-center justify-between bg-(--wa-bar) px-5 pt-2 text-[11px] font-semibold text-white">
        <span>{time(0)}</span>
        <span className="flex items-center gap-1">
          <span className="flex items-end gap-px">{[4, 6, 8, 10].map((h) => <i key={h} className="w-[3px] rounded-sm bg-white" style={{ height: h }} />)}</span>
          <span className="ml-1 w-5 h-2.5 rounded-[3px] border border-white/80 p-px"><i className="block h-full w-3/4 rounded-[1px] bg-white" /></span>
        </span>
      </div>
      <div className="flex items-center gap-2 bg-(--wa-bar) pl-1 pr-3 py-2 text-white">
        <Icon name="back" className="w-5 h-5" />
        <div className="w-9 h-9 shrink-0 rounded-full bg-linear-to-br from-emerald-300 to-teal-600 grid place-items-center text-sm font-semibold">{title[0]}</div>
        <div className="min-w-0 flex-1">
          <div className="text-[14.5px] font-medium leading-tight truncate">{title}</div>
          <div className="text-[11.5px] text-white/80 h-4 truncate">{typing ? t.typing : t.online}</div>
        </div>
        <Icon name="video" className="w-5 h-5" />
        <Icon name="phone" className="w-[18px] h-[18px] ml-2" />
        <Icon name="more" className="w-5 h-5" />
      </div>
      <div ref={box} className="wa-wall h-[390px] overflow-y-auto scroll-smooth px-3 py-3 text-[13.5px] leading-snug text-(--wa-text) [scrollbar-width:none]">
        <div className="mx-auto mb-3 w-fit rounded-md bg-(--wa-in) px-2.5 py-1 text-[11px] text-(--wa-meta) shadow-sm">{t.today}</div>
        {lines.map(([who, text], i) => {
          const out = who !== 'mitu', first = i === 0 || lines[i - 1][0] !== who
          const read = i < lines.length - 1 || typing
          return (
            <div key={i} className={cx('bubble relative flow-root w-fit max-w-[82%] rounded-[8px] px-2 pt-1.5 pb-1 shadow-[0_1px_.5px_rgba(11,20,26,.13)]',
              out ? 'ml-auto bg-(--wa-out) origin-bottom-right' : 'bg-(--wa-in) origin-bottom-left',
              first ? cx('mt-2', out ? 'wa-tail-out rounded-tr-none' : 'wa-tail-in rounded-tl-none') : 'mt-0.5')}>
              {text}
              <span className="float-right ml-2 mt-1.5 -mb-0.5 flex items-center gap-0.5 text-[10.5px] text-(--wa-meta)">
                {time(i)}
                {out && <Icon name="ticks" className={cx('w-4 h-4 transition-colors duration-500', read ? 'text-[#53bdeb]' : 'text-(--wa-meta)')} />}
              </span>
            </div>
          )
        })}
        {typing && (
          <div className="bubble wa-tail-in relative mt-2 w-fit rounded-[8px] rounded-tl-none bg-(--wa-in) px-3 py-3 flex gap-1 origin-bottom-left shadow-[0_1px_.5px_rgba(11,20,26,.13)]">
            {[0, 1, 2].map((d) => <span key={d} className="dot w-1.5 h-1.5 rounded-full bg-(--wa-meta)" style={{ animationDelay: `${d * 150}ms` }} />)}
          </div>
        )}
      </div>
      <div className="flex items-center gap-1.5 bg-(--wa-input) px-1.5 py-1.5">
        <div className="flex flex-1 items-center gap-2 rounded-full bg-(--wa-field) px-3 h-10 text-(--wa-meta)">
          <Icon name="smile" className="w-5 h-5 shrink-0" />
          <span className="flex-1 text-[14px] truncate">{t.message}</span>
          <Icon name="clip" className="w-[18px] h-[18px] shrink-0 -rotate-45" />
          <Icon name="camera" className="w-5 h-5 shrink-0" />
        </div>
        <span className="w-10 h-10 shrink-0 rounded-full bg-[#00a884] grid place-items-center text-white"><Icon name="mic" className="w-5 h-5" /></span>
      </div>
    </div>
  )
}

// Every word on the page, in English and Bangla. The café's chat stays in English in both: its customers are in Sydney.
const CAFE_CHAT = [
  ['them', '🎤 Voice note · 0:04'],
  ['mitu', 'Heard: “two flat whites, one with oat, for pickup.” Regular size? That’s $10.80, ready in 15 min.'],
  ['them', 'yes please!', 'order'],
  ['mitu', 'Order #87 confirmed ✓ Two flat whites, one oat, ready at the counter in 15 minutes.', 'ready'],
  ['them', 'is the risotto gluten free?'],
  ['mitu', 'The Mushroom Risotto has no gluten ingredients, but our kitchen handles flour, so I can’t promise it’s safe for coeliacs.'],
]

const COPY = {
  en: {
    locale: 'en', switch: 'বাংলা', switchLang: 'bn',
    nav: ['Features', 'Demos', 'How it works', 'Sign in'],
    online: 'online', typing: 'typing…', message: 'Message', today: 'Today',
    badge: 'Answering DMs right now', h1: ['Every DM answered.', 'Every order taken.'],
    lede: 'Mitu is an AI assistant for shops that sell on Facebook, Instagram and WhatsApp, and by text and phone call. It answers “price?”, takes the order, counts the stock and hands off to you when a customer needs a person.',
    cta: ['See it in action', 'What it does'], channels: ['WhatsApp', 'Messenger', 'Instagram', 'SMS', 'Phone calls'],
    floats: ['Order #1042 · ৳1,580', 'Replied in 2 s'],
    hero: [
      ['them', 'price?'],
      ['mitu', 'Indigo Block-print Kurti is ৳1,450. Sizes S to XL, 12 left 💙'],
      ['them', 'L available? COD outside Dhaka?'],
      ['mitu', 'Yes, L is in stock. Delivery outside Dhaka is ৳130, cash on delivery is fine. Total ৳1,580. Shall I place it?'],
      ['them', 'yes'],
      ['mitu', 'Done ✓ Order #1042 is placed. We’ll message you when it ships.'],
    ],
    asks: ['price?', 'is M available?', 'delivery charge outside Dhaka?', 'COD?', 'how much is this one? 📷',
      'any oat milk?', 'when will my order arrive?', 'is it gluten free?', '🎤 voice note', 'bKash number?'],
    features: ['Features', 'Your best salesperson, in every chat at once',
      'Most messages are the same few questions. Mitu answers them from your own catalogue and settings, and your team only sees what needs them.', [
        ['chats', 'Answers every DM', 'Prices, sizes, colours, what’s in stock, delivery charges, opening hours. Day and night, in seconds.'],
        ['orders', 'Takes orders end to end', 'Pickup or delivery, cash on delivery or bKash, today or a later day. Nothing is placed until the customer says yes.'],
        ['products', 'Never oversells', 'Stock counts down with every order and goes back up when one is cancelled. The last one is never sold twice.'],
        ['mic', 'Voice notes and photos', 'Customers can talk instead of type, in English or Bangla, or send a screenshot of a post: “how much is this one?”'],
        ['phone', 'Texts and phone calls', 'No app or data needed: customers text or call your number and Mitu answers out loud, then sends order updates by SMS.'],
        ['users', 'Hands off to a person', 'Complaints, refunds, odd requests or “can I talk to someone?” go to your team, and the AI steps back in that chat.'],
        ['kitchen', 'Live packing board', 'Orders land in New, Preparing and Ready columns with a sound. Pause online orders with one button.'],
        ['overview', 'Know your numbers', 'Revenue, top items, pickup vs delivery, how much the AI handled and what it cost, against last period.'],
      ]],
    demos: ['Business demos', 'One assistant, any business that sells in chat',
      'The same Mitu runs a clothing shop and a café. Pick one and watch a real conversation play out.', 'Meanwhile, in your dashboard', 'live'],
    demo: [
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
        lines: CAFE_CHAT,
        cards: {
          order: { icon: 'kitchen', title: 'Kitchen · New', lines: ['2 × Flat White (1 oat)', 'Pickup · 15 min', '$10.80'], tone: 'info' },
          ready: { icon: 'check', title: 'Customer told', lines: ['Order #87 · Pickup', 'Ready 15 min after ordering'], tone: 'ok' },
        },
      },
    ],
    how: ['How it works', 'Set up once, then it just answers', [
      ['products', 'Describe your business', 'Products, prices, stock, delivery zones, payment methods and hours, in one settings file or the dashboard.'],
      ['send', 'Connect your channels', 'Your WhatsApp number, Facebook Page, Instagram account and a phone number for texts and calls. Every channel runs the same assistant.'],
      ['overview', 'Watch the orders come in', 'Follow every chat live, step in with one click and see what sold.'],
    ]],
    stats: [[5, '', 'channels: WhatsApp, Messenger, Instagram, SMS, calls'], [2, '', 'kinds of business, one install'], [10, '', 'tools the AI can use'], [24, '/7', 'answering, even at 3 am']],
    end: ['Stop answering “price?” at midnight', 'Let Mitu take the DMs. You pack the orders.', 'Sign in to your dashboard'],
  },
  bn: {
    locale: 'bn-BD', switch: 'English', switchLang: 'en',
    nav: ['ফিচার', 'ডেমো', 'কীভাবে কাজ করে', 'সাইন ইন'],
    online: 'অনলাইন', typing: 'টাইপ করছে…', message: 'মেসেজ', today: 'আজ',
    badge: 'এই মুহূর্তে মেসেজের উত্তর দিচ্ছে', h1: ['সব মেসেজের উত্তর।', 'সব অর্ডার কনফার্ম।'],
    lede: 'মিতু হলো ফেসবুক, ইনস্টাগ্রাম, হোয়াটসঅ্যাপ, SMS আর ফোন কলে বিক্রি করা দোকানের AI অ্যাসিস্ট্যান্ট। “দাম কত?”-র উত্তর দেয়, অর্ডার নেয়, স্টক গোনে, আর কাস্টমারের মানুষের সাথে কথা বলা দরকার হলে আপনার হাতে তুলে দেয়।',
    cta: ['লাইভ দেখুন', 'কী কী করে'], channels: ['হোয়াটসঅ্যাপ', 'মেসেঞ্জার', 'ইনস্টাগ্রাম', 'SMS', 'ফোন কল'],
    floats: ['অর্ডার #১০৪২ · ৳১,৫৮০', '২ সেকেন্ডে উত্তর'],
    hero: [
      ['them', 'দাম কত?'],
      ['mitu', 'ইন্ডিগো ব্লক-প্রিন্ট কুর্তি ৳১,৪৫০। সাইজ S থেকে XL, আর ১২টা আছে 💙'],
      ['them', 'L আছে? ঢাকার বাইরে COD হবে?'],
      ['mitu', 'জি, L আছে। ঢাকার বাইরে ডেলিভারি ৳১৩০, ক্যাশ অন ডেলিভারি করা যাবে। মোট ৳১,৫৮০। অর্ডার কনফার্ম করব?'],
      ['them', 'হ্যাঁ'],
      ['mitu', 'হয়ে গেছে ✓ অর্ডার #১০৪২ কনফার্ম। পাঠানোর সময় আপনাকে জানিয়ে দেব।'],
    ],
    asks: ['দাম কত?', 'M সাইজ আছে?', 'ঢাকার বাইরে ডেলিভারি চার্জ কত?', 'COD হবে?', 'এটার দাম কত? 📷',
      'ওট মিল্ক আছে?', 'অর্ডার কবে পাব?', 'এটা কি গ্লুটেন ফ্রি?', '🎤 ভয়েস নোট', 'বিকাশ নম্বর?'],
    features: ['ফিচার', 'আপনার সেরা সেলসম্যান, একসাথে প্রতিটি চ্যাটে',
      'বেশিরভাগ মেসেজে ঘুরেফিরে একই কয়েকটা প্রশ্ন। মিতু আপনার নিজের ক্যাটালগ আর সেটিংস দেখে উত্তর দেয়, আর আপনার টিম শুধু সেটুকুই দেখে যেটা তাদের দেখা দরকার।', [
        ['chats', 'সব মেসেজের উত্তর', 'দাম, সাইজ, রং, স্টকে কী আছে, ডেলিভারি চার্জ, খোলার সময়। দিনরাত, কয়েক সেকেন্ডে।'],
        ['orders', 'শুরু থেকে শেষ পর্যন্ত অর্ডার', 'পিকআপ বা ডেলিভারি, ক্যাশ অন ডেলিভারি বা বিকাশ, আজ বা পরের কোনো দিন। কাস্টমার “হ্যাঁ” না বলা পর্যন্ত কোনো অর্ডার হয় না।'],
        ['products', 'কখনো বাড়তি বিক্রি করে না', 'প্রতিটি অর্ডারে স্টক কমে, ক্যানসেল হলে আবার বাড়ে। শেষ পিসটা কখনো দুবার বিক্রি হয় না।'],
        ['mic', 'ভয়েস নোট আর ছবি', 'কাস্টমার টাইপ না করে বাংলায় বা ইংরেজিতে কথা বলতে পারে, বা পোস্টের স্ক্রিনশট পাঠাতে পারে: “এটার দাম কত?”'],
        ['phone', 'SMS আর ফোন কল', 'অ্যাপ বা ইন্টারনেট লাগে না: কাস্টমার আপনার নম্বরে SMS বা কল করে, মিতু মুখে উত্তর দেয়, আর অর্ডারের আপডেট পাঠায় SMS-এ।'],
        ['users', 'মানুষের হাতে তুলে দেয়', 'অভিযোগ, রিফান্ড, অদ্ভুত অনুরোধ বা “কারো সাথে কথা বলতে পারি?” আপনার টিমের কাছে যায়, আর সেই চ্যাটে AI সরে দাঁড়ায়।'],
        ['kitchen', 'লাইভ প্যাকিং বোর্ড', 'অর্ডার আসে নতুন, প্রস্তুত হচ্ছে আর রেডি কলামে, সাথে একটা শব্দ। এক বাটনে অনলাইন অর্ডার বন্ধ রাখুন।'],
        ['overview', 'নিজের হিসাব জানুন', 'আয়, সবচেয়ে বেশি বিক্রি, পিকআপ বনাম ডেলিভারি, AI কতটা সামলাল আর খরচ কত, আগের সময়ের সাথে তুলনা করে।'],
      ]],
    demos: ['বিজনেস ডেমো', 'একটাই অ্যাসিস্ট্যান্ট, চ্যাটে বিক্রি করা যেকোনো ব্যবসার জন্য',
      'একই মিতু চালায় একটা কাপড়ের দোকান আর একটা ক্যাফে। একটা বেছে নিন আর আসল কথোপকথন দেখুন।', 'এদিকে, আপনার ড্যাশবোর্ডে', 'লাইভ'],
    demo: [
      {
        id: 'shop', label: 'কাপড়ের দোকান', name: 'Nakshi Threads', where: 'ঢাকা · ফেসবুক, ইনস্টাগ্রাম, হোয়াটসঅ্যাপ',
        blurb: 'মেসেজে কুর্তি, শাড়ি আর পাঞ্জাবি বিক্রি করে। স্টক নিজে থেকে কমে, ডেলিভারি জোন, ক্যাশ অন ডেলিভারি বা বিকাশ।',
        lines: [
          ['them', 'এটার দাম কত? 📷 [পোস্টের স্ক্রিনশট]'],
          ['mitu', 'এটা মাস্টার্ড এমব্রয়ডারি কুর্তি, ৳১,৬৫০। মাত্র ৩টা বাকি! কোন সাইজ লাগবে?'],
          ['them', 'M. ঢাকার বাইরে ডেলিভারি চার্জ কত?'],
          ['mitu', 'কুরিয়ারে ৳১৩০, ২–৪ দিন। ক্যাশ অন ডেলিভারি বা বিকাশ। মোট ৳১,৭৮০। কনফার্ম করব?'],
          ['them', 'বিকাশ। কনফার্ম', 'order'],
          ['mitu', 'অর্ডার #১০৪৩ কনফার্ম ✓ ০১৭০০-০০০০০০ নম্বরে ৳১,৭৮০ পাঠিয়ে ট্রানজেকশন আইডি দিন।', 'stock'],
        ],
        cards: {
          order: { icon: 'orders', title: 'নতুন অর্ডার #১০৪৩', lines: ['মাস্টার্ড এমব্রয়ডারি কুর্তি · M', 'ঢাকার বাইরে · বিকাশ', '৳১,৭৮০'], tone: 'info' },
          stock: { icon: 'products', title: 'স্টক আপডেট', lines: ['মাস্টার্ড এমব্রয়ডারি কুর্তি', '৩ → ২টা বাকি'], tone: 'warn' },
        },
      },
      {
        id: 'cafe', label: 'ক্যাফে', name: 'Little Fern Café', where: 'নিউটাউন, সিডনি · হোয়াটসঅ্যাপ',
        blurb: 'কফি, নাশতা, আর বৃহস্পতি থেকে শনিবার ডিনার। দরকারি অপশনসহ পিকআপ অর্ডার, অ্যালার্জেন, ভয়েস নোট।',
        lines: CAFE_CHAT,
        cards: {
          order: { icon: 'kitchen', title: 'কিচেন · নতুন', lines: ['২ × Flat White (১টা ওট)', 'পিকআপ · ১৫ মিনিট', '$10.80'], tone: 'info' },
          ready: { icon: 'check', title: 'কাস্টমারকে জানানো হয়েছে', lines: ['অর্ডার #87 · পিকআপ', 'অর্ডারের ১৫ মিনিট পরে রেডি'], tone: 'ok' },
        },
      },
    ],
    how: ['কীভাবে কাজ করে', 'একবার সেট আপ করুন, তারপর নিজেই উত্তর দেয়', [
      ['products', 'আপনার ব্যবসার তথ্য দিন', 'পণ্য, দাম, স্টক, ডেলিভারি জোন, পেমেন্টের উপায় আর খোলার সময়, একটা সেটিংস ফাইলে বা ড্যাশবোর্ডে।'],
      ['send', 'চ্যানেল যুক্ত করুন', 'আপনার হোয়াটসঅ্যাপ নম্বর, ফেসবুক পেজ, ইনস্টাগ্রাম অ্যাকাউন্ট আর SMS ও কলের জন্য একটা ফোন নম্বর। সব চ্যানেলে একই অ্যাসিস্ট্যান্ট চলে।'],
      ['overview', 'অর্ডার আসা দেখুন', 'প্রতিটি চ্যাট লাইভ দেখুন, এক ক্লিকে নিজে কথা বলুন আর দেখুন কী বিক্রি হলো।'],
    ]],
    stats: [[5, '', 'চ্যানেল: হোয়াটসঅ্যাপ, মেসেঞ্জার, ইনস্টাগ্রাম, SMS, কল'], [2, '', 'ধরনের ব্যবসা, একটাই ইনস্টল'], [10, '', 'টুল, যা AI ব্যবহার করতে পারে'], [24, '/৭', 'উত্তর দেয়, রাত ৩টায়ও']],
    end: ['মাঝরাতে “দাম কত?”-র উত্তর দেওয়া বন্ধ করুন', 'মেসেজ সামলাক মিতু। আপনি অর্ডার প্যাক করুন।', 'আপনার ড্যাশবোর্ডে সাইন ইন করুন'],
  },
}

const TONE = { info: 'text-info bg-info/10', ok: 'text-ok bg-ok/10', warn: 'text-warn bg-warn/10' }

function Demo({ t }) {
  const [tab, setTab] = useState(0)
  const d = t.demo[tab]
  const [lines, typing] = useScript(d.lines, d.id + t.locale)
  const lit = new Set(lines.map((l) => l[2]).filter(Boolean))
  return (
    <div>
      <div role="tablist" className="mx-auto flex w-fit gap-1 rounded-2xl border border-line bg-surface p-1">
        {t.demo.map((x, i) => (
          <button key={x.id} role="tab" aria-selected={i === tab} onClick={() => setTab(i)}
            className={cx('rounded-xl px-4 h-9 text-sm font-medium transition', i === tab ? 'bg-ink text-inverse' : 'text-muted hover:text-ink')}>
            {x.label}
          </button>
        ))}
      </div>
      <div key={d.id} className="enter mt-10 grid items-center gap-10 lg:grid-cols-[1fr_300px_1fr]">
        <div className="text-center lg:text-left">
          <p className="text-xs font-medium uppercase tracking-widest text-brand">{d.where}</p>
          <h3 className="mt-2 text-2xl font-semibold tracking-tight">{d.name}</h3>
          <p className="mt-3 text-muted leading-relaxed">{d.blurb}</p>
        </div>
        <Phone title={d.name} t={t} lines={lines} typing={typing} className="mx-auto" />
        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-widest text-faint text-center lg:text-left">{t.demos[3]}</p>
          {Object.entries(d.cards).map(([k, c]) => (
            <div key={k} className={cx('card p-4 flex gap-3 transition-all duration-500', lit.has(k) ? 'opacity-100 translate-x-0 shadow-lg' : 'opacity-25 translate-x-4')}>
              <span className={cx('w-9 h-9 shrink-0 rounded-xl grid place-items-center', TONE[c.tone])}><Icon name={c.icon} /></span>
              <div className="min-w-0">
                <div className="text-sm font-medium">{c.title}</div>
                {c.lines.map((l) => <div key={l} className="text-sm text-muted">{l}</div>)}
              </div>
              {lit.has(k) && <span className="ml-auto self-start chip bg-brand-soft text-brand pop">{t.demos[4]}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const Brand = () => (
  <a href="#top" className="flex items-center gap-2.5">
    <span className="w-8 h-8 rounded-[10px] bg-linear-to-br from-emerald-400 to-teal-600 grid place-items-center text-white font-semibold text-sm shadow-sm shadow-emerald-600/30">M</span>
    <span className="font-semibold tracking-tight">Mitu</span>
  </a>
)

// The first visit picks Bangla when the browser asks for it; after that the viewer's choice is remembered.
const savedLang = () => {
  try { return localStorage.getItem('lang') || (navigator.language.startsWith('bn') ? 'bn' : 'en') } catch { return 'en' }
}

export default function Landing() {
  const [lang, setLang] = useState(savedLang)
  const t = COPY[lang]
  const pick = (l) => { setLang(l); try { localStorage.setItem('lang', l) } catch { /* private mode */ } }
  const [lines, typing] = useScript(t.hero, 'hero' + lang)
  return (
    <div id="top" lang={lang} className="min-h-dvh overflow-x-clip">
      <nav className="sticky top-0 z-30 border-b border-line/60 bg-canvas/75 backdrop-blur-lg">
        <div className="mx-auto max-w-6xl flex items-center justify-between h-16 px-4 md:px-8">
          <Brand />
          <div className="flex items-center gap-1">
            <a href="#features" className="hidden md:inline-flex btn btn-ghost">{t.nav[0]}</a>
            <a href="#demos" className="hidden md:inline-flex btn btn-ghost">{t.nav[1]}</a>
            <a href="#how" className="hidden md:inline-flex btn btn-ghost">{t.nav[2]}</a>
            <button lang={t.switchLang} onClick={() => pick(t.switchLang)} className="btn btn-secondary">{t.switch}</button>
            <a href="#/login" className="btn btn-primary ml-1">{t.nav[3]}</a>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <header className="relative">
        <div className="blob absolute -top-40 -left-40 w-[36rem] h-[36rem] rounded-full bg-emerald-400/25 blur-3xl" />
        <div className="blob absolute top-40 -right-40 w-[30rem] h-[30rem] rounded-full bg-teal-300/20 blur-3xl" style={{ animationDelay: '-6s' }} />
        <div className="relative mx-auto max-w-6xl grid gap-14 px-4 md:px-8 pt-16 pb-20 lg:grid-cols-[1.15fr_1fr] lg:items-center lg:pt-24">
          <div key={lang}>
            <p className="rise chip bg-brand-soft text-brand px-3! py-1!"><span className="w-1.5 h-1.5 rounded-full bg-brand pulse" /> {t.badge}</p>
            <h1 className="rise mt-6 text-4xl sm:text-5xl lg:text-6xl font-semibold tracking-tight leading-[1.05]" style={{ animationDelay: '80ms' }}>
              {t.h1[0]}<br /><span className="shimmer bg-clip-text text-transparent">{t.h1[1]}</span>
            </h1>
            <p className="rise mt-6 max-w-xl text-lg text-muted leading-relaxed" style={{ animationDelay: '160ms' }}>{t.lede}</p>
            <div className="rise mt-8 flex flex-wrap gap-3" style={{ animationDelay: '240ms' }}>
              <a href="#demos" className="btn btn-primary h-11! px-5!">{t.cta[0]}</a>
              <a href="#features" className="btn btn-secondary h-11! px-5!">{t.cta[1]}</a>
            </div>
            <div className="rise mt-10 flex flex-wrap gap-2 text-sm text-muted" style={{ animationDelay: '320ms' }}>
              {t.channels.map((c) => <span key={c} className="chip border border-line bg-surface px-3! py-1!">{c}</span>)}
            </div>
          </div>
          <div className="relative mx-auto w-[300px] max-w-full">
            <Phone title="Nakshi Threads" t={t} lines={lines} typing={typing} className="float" />
            <div className="float-card absolute -left-16 bottom-3 hidden sm:flex card px-3 py-2 items-center gap-2 shadow-xl text-sm whitespace-nowrap">
              <span className="text-ok"><Icon name="check" /></span> {t.floats[0]}
            </div>
            <div className="float-card absolute -right-14 top-3 hidden sm:flex card px-3 py-2 items-center gap-2 shadow-xl text-sm whitespace-nowrap" style={{ animationDelay: '-3s' }}>
              <span className="text-brand"><Icon name="bot" /></span> {t.floats[1]}
            </div>
          </div>
        </div>
      </header>

      {/* What customers ask, scrolling */}
      <div className="border-y border-line bg-surface py-5 overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_10%,#000_90%,transparent)]">
        <div className="marquee flex w-max gap-3">
          {[...t.asks, ...t.asks].map((q, i) => (
            <span key={i} className="rounded-full bg-subtle px-4 py-2 text-sm text-muted whitespace-nowrap">{q}</span>
          ))}
        </div>
      </div>

      {/* Features */}
      <section id="features" className="mx-auto max-w-6xl px-4 md:px-8 py-24 scroll-mt-16">
        <Reveal className="max-w-2xl">
          <p className="text-sm font-medium text-brand">{t.features[0]}</p>
          <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">{t.features[1]}</h2>
          <p className="mt-4 text-muted leading-relaxed">{t.features[2]}</p>
        </Reveal>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {t.features[3].map(([icon, title, text], i) => (
            <Reveal key={icon} delay={(i % 3) * 90} className="feature card p-6">
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
            <p className="text-sm font-medium text-brand">{t.demos[0]}</p>
            <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">{t.demos[1]}</h2>
            <p className="mt-4 text-muted leading-relaxed">{t.demos[2]}</p>
          </Reveal>
          <Demo t={t} />
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto max-w-6xl px-4 md:px-8 py-24 scroll-mt-16">
        <Reveal className="text-center max-w-2xl mx-auto">
          <p className="text-sm font-medium text-brand">{t.how[0]}</p>
          <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight">{t.how[1]}</h2>
        </Reveal>
        <div className="relative mt-14 grid gap-6 md:grid-cols-3">
          <div className="flow hidden md:block absolute top-8 left-[16%] right-[16%] h-0.5" />
          {t.how[2].map(([icon, title, text], i) => (
            <Reveal key={icon} delay={i * 150} className="relative text-center">
              <span className="relative mx-auto w-16 h-16 rounded-2xl bg-ink text-inverse grid place-items-center shadow-lg"><Icon name={icon} className="w-6 h-6" />
                <span className="absolute -top-2 -right-2 w-6 h-6 rounded-full bg-brand text-inverse text-xs font-semibold grid place-items-center">{(i + 1).toLocaleString(t.locale)}</span>
              </span>
              <h3 className="mt-6 font-medium">{title}</h3>
              <p className="mt-2 text-sm text-muted leading-relaxed max-w-xs mx-auto">{text}</p>
            </Reveal>
          ))}
        </div>
        <div className="mt-20 grid grid-cols-2 md:grid-cols-4 gap-4">
          {t.stats.map(([n, s, label]) => (
            <Reveal key={n} className="card p-6 text-center">
              <div className="text-4xl font-semibold tracking-tight text-brand tabular-nums"><CountUp to={n} suffix={s} locale={t.locale} /></div>
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
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight">{t.end[0]}</h2>
            <p className="mt-4 text-white/60 max-w-xl mx-auto">{t.end[1]}</p>
            <a href="#/login" className="btn mt-8 h-11! px-6! bg-white text-[#0b1512] hover:bg-white/90">{t.end[2]}</a>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-line py-8 px-4 md:px-8">
        <div className="mx-auto max-w-6xl flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-faint">
          <Brand />
          <span>© {new Date().getFullYear().toLocaleString(t.locale, { useGrouping: false })} Mitu</span>
        </div>
      </footer>
    </div>
  )
}
