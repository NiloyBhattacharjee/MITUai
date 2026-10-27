# Mitu: an AI assistant for shops that sell in DMs on Facebook, Instagram and WhatsApp

Small sellers on Facebook, Instagram and WhatsApp have no website or checkout: every sale happens in DMs, and most messages are "price?", "is M available?", "delivery charge outside Dhaka?" and "COD?". Mitu answers them in chat, day and night. It answers product questions (prices, sizes and colours, what's left in stock, delivery charges), takes orders for delivery or pickup with cash on delivery or bKash (for today or a later day), counts stock down so the last one is never sold twice, understands voice notes and photos (a screenshot of a post: "how much is this one?"), and hands off to a person when it should. The demo business is **Nakshi Threads**, a clothing shop in Dhaka, set up in `business.yaml`.

One install runs many businesses, each with its own products, settings, staff, chats, orders and WhatsApp/Messenger/Instagram accounts. The same code also runs a café (table bookings, allergens) and a salon (appointments with named staff): see `examples/` and [What a business does](#what-a-business-does).

- **Dashboard** (home page; staff sign in with their business's ID and their own password):
  - **Overview:** revenue per day, orders, bookings, top items, pickup vs delivery, AI-handled share and AI cost, each compared with the previous period.
  - **Kitchen** (Packing for a shop): live orders in New / Preparing / Ready columns, with a sound when one arrives and a button to pause online orders.
  - **Orders:** every order, with a CSV export.
  - **Bookings:** the next two weeks of table bookings or appointments (not shown for a shop).
  - **Approvals:** refunds, discounts, big-group bookings and other exceptions the assistant won't decide alone. Staff approve or decline with an optional message, and the customer is told in their chat; an approved big group is booked automatically.
  - **Conversations:** live chats with staff replies (with saved replies from `business.yaml`) and an AI on/off switch per chat. Under each customer message, "AI used …" shows the tools the AI called, what they returned and what that turn cost.
  - **Menu** (Products for a shop, Services for a salon): prices, options, stock, dietary info, photos, and a sold-out switch.
- **Chat simulator** (`#/simulator`, signed-in staff only, since every message runs the paid LLM): a WhatsApp-style phone in the browser, with a hold-to-record mic and a photo button.
- **Real chats:** WhatsApp through the Cloud API webhook; Messenger and Instagram through the Messenger Platform webhook.

Every channel runs the same code path: `pipeline.py` → `agent.respond()` → LLM with tools.

```
business.yaml          the demo shop: hours, delivery, payments, fixed messages, products (each business on the install has its own)
examples/              other kinds of business: a café (orders, table bookings; the tests and the eval use it) and a salon (appointments)
backend/
  app/config.py        .env, money helpers
  app/tenancy.py       which business a request or message belongs to; every query on a business's data is filtered to it
  app/auth.py          staff passwords (scrypt) and login sessions
  app/shops.py         creating businesses from business.yaml files
  app/db.py            SQLAlchemy models (SQLite by default)
  app/hours.py         opening hours in the business's timezone
  app/options.py       menu options ("Milk: Full cream, Oat +0.80") and checking a customer's choices
  app/tools.py         the AI's tools: search_menu, get_menu, get_item, send_item_photo, saved_details, create_order,
                       get_order_status, cancel_order, check_availability, book_table, my_bookings, cancel_booking,
                       request_approval, get_business_info, escalate_to_human
  app/llm.py           LLM interface: Bedrock, the Anthropic API or Ollama (LLM_PROVIDER)
  app/agent.py         system prompt, history, tool loop, handoff
  app/pipeline.py      one entry point per message type (text / voice / photo / other), used by every channel
  app/whatsapp.py      WhatsApp webhook (verify, signature, dedupe) + Graph API client
  app/messenger.py     Messenger and Instagram webhook + Send API
  app/voice.py         speech-to-text / text-to-speech (OpenAI, Amazon Transcribe), ffmpeg helpers
  app/main.py          FastAPI: dashboard + simulator API
  manage.py            add businesses and staff, connect their WhatsApp number and Facebook Page
  seed.py  cli.py  eval.py  backup.py  compare_stt.py  tests/  evals/
frontend/              React + Vite + Tailwind (dashboard + simulator), served by Caddy in Docker
```

## 1. Run locally

You need Python 3.12+, Node 20+ and **ffmpeg** on your PATH (ffmpeg is only needed for voice notes).

```bash
cp .env.example .env           # then fill in at least the LLM credentials, SHOP_ID and ADMIN_PASSWORD

# backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python seed.py                         # creates the first business (SHOP_ID) from business.yaml and .env,
                                                 # with a few sample orders, bookings or appointments, whichever it takes
                                                 # (add --demo for 60 days of fake ones to fill the Overview)
.venv/bin/python cli.py 0412345678               # optional: chat with the first business's agent in the terminal
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                                      # http://localhost:5173
```

Open http://localhost:5173 and sign in with the business ID (`SHOP_ID`, e.g. `nakshi`) and `ADMIN_PASSWORD`. `seed.py` refuses passwords under 8 characters or like `admin` and `change-me`. The simulator is at `#/simulator` once you're signed in, and talks to your business.

**Coming from an older version** (from before one install ran several businesses)? Its database doesn't fit, and the server will say so. Move `backend/shop.db` (and `backend/media/`) aside, or point `DATABASE_URL` at a new file, then run `seed.py`.

Run the tests (no credentials needed, the LLM and Graph API are mocked, and the business's clock is pinned to 10:00). They include checks that one business can never see or change another's data, through any page or webhook:

```bash
cd backend && .venv/bin/python -m pytest
```

The tests use a fake LLM, so they can't tell you whether the real model behaves. The eval does: 35 scripted customer conversations in `backend/evals/cases.yaml`, on the café in `examples/cafe.yaml` (shop and salon cases are issue #6) (menu questions, allergies, ordering, required options, closing time, cancelling, bookings, refunds and big groups going to the team, complaints, prompt injection, …) run against the real model from `.env`. Each runs on a throwaway database at 10:00 café time, checking which tools were called, what ended up in the database, and the replies. Run it after changing the prompt, the tools, the menu format or the model. It costs roughly $1–3 per full run on Sonnet (about double on Opus, free on Ollama):

```bash
cd backend && .venv/bin/python eval.py              # all cases; exit code 1 if any fail
.venv/bin/python eval.py booking cancel             # only cases whose name contains a word
```

Models vary from run to run, so a single failure is worth reading (the transcript is printed) before you change anything. Add a case whenever a real chat goes wrong.

**Docker:** `cp .env.example .env`, fill it in, then `docker compose up --build` and open http://localhost (port 80 already taken? `HTTP_PORT=8080 docker compose up --build` and open http://localhost:8080). Rebuild with `--build` after pulling new code: plain `docker compose up` runs the old images. Docker inside WSL2 with Ollama on Windows: add `COMPOSE_FILE=docker-compose.yml:docker-compose.ollama.yml` to `.env` (see that file). The first start creates the first business from `.env` and `business.yaml`. Caddy serves the app and passes `/api`, `/media` and the webhooks to the backend, which has no public port. Seed data loads on first start, and the database and media live in the `data` volume. A `backup` container writes a `.tar.gz` of the database and media to `./backups` at start and then daily, keeping 14.

### LLM settings

- `LLM_PROVIDER=ollama` (default) runs a local model through [Ollama](https://ollama.com), with no cloud account: `ollama pull qwen3:4b`, or set `OLLAMA_MODEL` to another one. It needs a model with tool support (and a vision model, like `qwen2.5vl`, for customer photos). Good for trying the flow for free; expect more mistakes and slow replies on a CPU.
- `LLM_PROVIDER=bedrock` uses boto3 `bedrock-runtime` `converse` with tool use, with the model from `BEDROCK_MODEL_ID` and the standard AWS credentials. Enable Claude model access in the Bedrock console first. If your region needs an inference profile, use the prefixed ID (e.g. `us.anthropic.claude-opus-5-5`).
- `LLM_PROVIDER=anthropic` uses the Anthropic API with `ANTHROPIC_API_KEY`. It runs at `LLM_EFFORT=low` for snappy chat replies, prompt caching is on, and server-side refusal fallbacks are on (`fallbacks: "default"`), so a false-positive safety decline is retried on another model instead of failing.

## 2. Connect real WhatsApp (Meta test number + ngrok)

1. Go to https://developers.facebook.com → **My Apps → Create app**. Choose **Other → Business**, give it a name and create it.
2. In the app dashboard, find **WhatsApp** and click **Set up**. Pick or create a Meta Business account when asked.
3. Open **WhatsApp → API Setup**. Meta gives you a **test phone number** and a **temporary access token** (valid 24 hours).
   - Copy the token into `WHATSAPP_TOKEN`.
   - Copy the **Phone number ID** (not the phone number itself) into `WHATSAPP_PHONE_NUMBER_ID`.
   - Under **To**, add your own WhatsApp number and confirm the code Meta sends. The test number can only message up to 5 verified numbers.
4. Go to **App settings → Basic**, click **Show** next to **App secret**, and copy it into `WHATSAPP_APP_SECRET`. The webhook rejects any request without a valid `X-Hub-Signature-256`.
5. Set `WHATSAPP_VERIFY_TOKEN` in `.env` to any random string you choose, then restart the backend.
6. Expose the backend: `ngrok http 8000`. Copy the `https://….ngrok-free.app` URL.
7. In **WhatsApp → Configuration → Webhook**, click **Edit**:
   - Callback URL: `https://<your-ngrok-url>/webhook`
   - Verify token: the same value as `WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and save**. Meta calls `GET /webhook` and the backend echoes the challenge.
8. Still under Webhook fields, **Subscribe** to `messages`.
9. Send a WhatsApp message from your verified number to the test number. The reply comes from Mitu, and the chat appears in the dashboard.

`seed.py` copies `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` (and `PAGE_ACCESS_TOKEN`, `PAGE_ID`, `INSTAGRAM_ID`) into the first business when it creates it. To change them after that, use `python manage.py connect <business ID> --whatsapp-token ...`; editing `.env` won't change an existing business.

For anything beyond a demo, create a **System User** in Business Settings and generate a permanent token instead of the 24-hour one. A real business number also needs Meta business verification.

## 3. Set up a business

Each business is described by a file like `business.yaml` (the first business uses `BUSINESS_FILE`, default `business.yaml`):

- **The basics:** name, the assistant's name, `timezone`, `currency`, location (add parking and transport, since people ask), about.
- **`hours`:** per day, e.g. `"07:00-15:00"` or `"07:00-11:00, 17:00-21:00"`; leave a day out to close it. Orders and bookings are only accepted inside these. `prep_minutes` is the earliest an order can be ready, and `quote_reminder_minutes` is when a customer who hasn't answered an order summary gets one nudge.
- **`fulfilment`:** pickup and delivery, with fees and notes. `needs_address: true` makes the assistant ask for an address.
- **`payment_methods`:** with only one, the assistant never asks.
- **`reservations`:** seats (`capacity`), how long a sitting lasts, the biggest party it books itself (bigger ones go to a person), the last booking before closing, and how far ahead people can book.
- **`allergen_note`, the fixed replies, and the messages customers get** when staff move an order on, cancel a booking or answer an approval request.
- **`saved_replies`:** quick replies staff can insert in the chat box.
- **`menu`:** each item has an `id`, `name`, `category`, `price`, `description`, `options`, `dietary`, `allergens`, `aliases` (other names customers use) and optionally `available: false`. Options are one line per group: `"Size*: Regular, Large +0.80"` (`*` = must pick one), `"Milk: Full cream, Oat +0.80"` (at most one), `"Extras (any): Extra shot +0.60"` (any number). The dashboard's Menu page uses the same format.

For the first business, run `python seed.py`. After that, manage the menu on the Menu page; re-running `seed.py` (or `manage.py update-shop`) only adds items that aren't in the database yet.

## 4. Run several businesses on one install

Every business gets its own menu, settings, staff, chats, orders, bookings, approvals and AI-cost figures. Staff sign in with their business's ID and their own password and only ever see their business. The LLM and speech keys in `.env` are shared, and the Overview page shows what each business's AI costs, which is what you'd bill on.

```bash
cd backend
.venv/bin/python manage.py add-shop harbour-bistro harbour.yaml --owner-password 'a-long-password'
.venv/bin/python manage.py add-staff harbour-bistro Sam 'sams-password'
.venv/bin/python manage.py connect harbour-bistro --whatsapp-number-id 1234567890 --whatsapp-token EAAG... \
                                                  --page-id 9876543210 --instagram-id 1784... --page-token EAAG...
.venv/bin/python manage.py update-shop harbour-bistro harbour.yaml   # after editing its settings file
.venv/bin/python manage.py list
```

All businesses share one Meta app (`WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`) and the same webhook URLs. Each WhatsApp message is routed to the business whose number it was sent to (`phone_number_id`), and each Messenger or Instagram message to the business whose Page or account received it. With a single business on the install, everything goes to it, so the ids are optional. To put your customers' own numbers on your Meta app, Meta requires you to join its Tech Provider program and use Embedded Signup; until then, each business can use a number on your own Meta business account.

### What a business does

What a business does follows from the sections in its settings file, and the assistant, its tools and the dashboard's pages follow from that:

| Section | Turns on |
|---|---|
| `fulfilment` + `payment_methods` | orders (pickup, delivery zones, any payment methods, e.g. cash on delivery and bKash) and the Kitchen (or Packing) and Orders pages |
| `reservations` | table bookings |
| `appointments` | services by appointment with named staff (`providers`), for items that have `minutes` |

On items, `stock: 12` makes the assistant count them down (it never sells more than are left, and a cancelled order puts them back), and `orders_days_ahead: 7` lets customers order for a later day. `kind: a hair and beauty salon` is how the assistant describes the business. `labels` renames dashboard pages (`menu: Catalogue`), and `try_messages` are the examples the chat simulator offers. `business.yaml` is a working shop; `examples/cafe.yaml` and `examples/salon.yaml` are the others.

```bash
.venv/bin/python manage.py add-shop bloom ../examples/salon.yaml --owner-password 'a-long-password'
```

## 5. CI/CD and deploying

`.github/workflows/ci-cd.yml` runs on every push and pull request:

1. **Backend lint and tests** (`ruff check .`, then `pytest`, fake LLM and mocked Meta API, so no secrets needed) and **frontend build**.
2. On `main` only: builds the backend and frontend Docker images and pushes them to GitHub Container Registry
   (`ghcr.io/<owner>/<repo>-backend` / `-frontend`, tagged `latest` and the commit SHA).
3. Deploys them over SSH with `docker compose pull && up -d`, once these repository secrets exist
   (Settings → Secrets and variables → Actions):

| Secret | What |
|---|---|
| `ENV_FILE` | The whole production `.env` (API keys, the first business's `SHOP_ID`, `ADMIN_PASSWORD`, `STAFF` and tokens, `PUBLIC_URL`, `SITE_ADDRESS`) |
| `DEPLOY_HOST` | Server address; without it the deploy step is skipped |
| `DEPLOY_USER` | SSH user that can run `docker` |
| `DEPLOY_SSH_KEY` | That user's private key |

Update the production settings with `gh secret set ENV_FILE < .env.production` and re-run the workflow.
On the server, set `SITE_ADDRESS` in `ENV_FILE` to your domain and point the domain's DNS at the server with ports 80 and 443 open: Caddy gets and renews the HTTPS certificate itself. Your webhook URLs are then `https://<domain>/webhook` and `https://<domain>/messenger/webhook`.

Add more businesses on the server with `docker compose exec backend python manage.py ...` (their business files can sit in the `data` volume).

Backups land in `~/mituai/backups` on the server. **Copy them off the server** (e.g. a nightly `rclone copy` or `aws s3 sync` from cron): a backup on the same disk doesn't survive losing the server. To restore, stop the stack, put `shop.db` and `media/` from an archive back into the `data` volume, and start it again.

## Voice notes

The flow: WhatsApp `audio` message → `GET /{media_id}` → download with the token → save the original (for the dashboard player) → ffmpeg to 16 kHz mono WAV → reject anything over 2 minutes → speech-to-text → if the transcript is empty or confidence is below `STT_MIN_CONFIDENCE`, reply "please repeat or type it" (never guess) → otherwise the same `agent.respond()` as typed text. The transcript is stored marked as a voice note, and each step's time is logged.

- `STT_PROVIDER=openai`: OpenAI `whisper-1`, with a prompt listing the menu's item names so it spells them right. Confidence comes from segment log-probabilities.
- `STT_PROVIDER=aws`: Amazon Transcribe batch in `TRANSCRIBE_LANGUAGE` (default `en-US`). It uploads to `TRANSCRIBE_S3_BUCKET`, runs a job, polls, then deletes the object and the job.
- To compare both on the same recordings, run `python compare_stt.py backend/media/*.ogg`. It prints text, detected language, confidence and latency per provider.
- `VOICE_REPLY=true` also sends a spoken reply (OpenAI TTS → ffmpeg to OGG/Opus → `POST /{phone_number_id}/media` → audio message). The text version is always sent too.

## Decisions

Simplest options picked where the brief was open:

- **Order confirmation is enforced in code, not just the prompt.** `create_order(confirmed=false)` checks everything and quotes, and stores the quote on the chat. `confirmed=true` only works if that quote was made in an *earlier* turn, so the customer must have sent a message after seeing the summary. Whether that message means "yes" is the LLM's call. Confirming checks everything again (sold out since? kitchen closed? orders paused?), and if the price changed it shows the new total and asks again.
- **The demo is a shop that sells in DMs, not a café.** Cafés mostly take orders through delivery apps, so chat is a side channel for them; for Facebook and Instagram sellers the chat is the whole shop. The café stays as `examples/cafe.yaml`, and the tests and eval still run on it.
- **Orders are for today only** by default, for "as soon as possible" (now plus `prep_minutes`) or a time today inside opening hours. A shop that ships sets `orders_days_ahead`; the time must still be inside its hours that day. There's no capacity per day: add one when a business gets more orders than it can make.
- **Bookings are one step** (the prompt makes the assistant repeat the details and wait for a yes): a wrong booking is cheap to cancel, unlike a wrong food order. Capacity is a seat count: every booking within one sitting of the new one counts against it. That over-counts slightly; a table plan would be exact.
- **Approvals vs handoff:** a request with a clear yes/no (refund, discount, big group) goes to the Approvals queue and the assistant keeps chatting; an upset customer, a serious allergy question or catering goes to a person and the assistant steps back. Two staff can't both answer one request: the decision is a conditional update.
- **One reminder** per unanswered order summary, checked every minute by the backend and sent only inside the messaging window. It's marked before sending, so a failed send never becomes a second nudge.
- **Sold out is a switch; stock counts are optional.** Cafés run out of things during the day and nobody counts croissants into a system, so items without `stock` just have the switch. A shop sets `stock`: placing an order counts it down with a conditional update (two customers can't both buy the last one), and cancelling, by the customer or on the dashboard, puts it back.
- **Appointments are bookings with a service and a person.** A service is a menu item with `minutes`, so search, photos, prices and the dashboard's Menu page all work for it. The assistant offers the start times when someone who does that service is free for its whole length inside opening hours, and books the first free person unless the customer asked for one. Everyone works the business's hours; per-person hours and days off aren't built yet.
- **Allergens:** the assistant only repeats the tags on each item plus the business's allergen note, never says a dish is safe, and hands serious allergy questions to a person.
- **Time:** each customer message in the history starts with its local send time (`[Sat 12 Oct 2026 13:05]`), so "tomorrow" and "in 20 minutes" work. It's part of the stored message, so the cached prompt prefix doesn't change from turn to turn.
- **Money** is stored in cents and shown with `currency` from `business.yaml`.
- **History is text only**, except the customer's own photos (JPG/PNG), which are re-sent as images every turn (prompt caching makes the repeats cheap). Each turn's tool calls stay inside that turn, and the model re-looks things up next turn. The last 30 messages are sent.
- **Every business's data is filtered in one place.** Business tables share a `Scoped` base. A SQLAlchemy hook adds `shop_id = <business in scope>` to every SELECT, UPDATE and DELETE on them, new rows get it automatically, and a query with no business in scope raises instead of returning everything. One forgotten filter can't leak another business's orders. Dashboard requests get their business from the login session, webhooks from the number or Page they were sent to, and background jobs loop over businesses.
- **Customers can only see and change their own orders and bookings,** the ones made in the same chat. Nothing is looked up by a phone number someone types.
- **Handoff** sets `needs_human` and switches the AI off for that chat. Turning it back on in the dashboard clears the flag. If the LLM call fails, the customer gets a polite fixed message and the chat is flagged.
- **Fixed replies** (unsupported media, unclear or too-long voice note, errors) skip the LLM and come from `business.yaml`.
- **`% handled by AI`** counts the period's chats that were never flagged and got no staff reply.
- **"Today"** uses `timezone` from `business.yaml`.
- **Live updates** use polling (1–5 s), not websockets. The kitchen sound plays when the page is open and its polling sees a new order (browsers pause polling in background tabs, so keep the kitchen screen in front).
- **Dashboard auth:** each person has their own password (scrypt-hashed) at their business, and signing in returns a session token, valid for 30 days, that the dashboard sends with every request. Only the token's SHA-256 is stored. 10 wrong passwords from one IP lock that IP out for 15 minutes; it's per IP, so a guesser can't lock the owner out. The lockout is in memory and resets on restart. Each business's Meta tokens are stored in the database as plain text; encrypt them at rest if the database ever leaves the server. Media files under `/media` are public but have random 128-bit names.
- **The simulator** runs in background tasks like WhatsApp and the page polls, so it exercises the exact same path. Simulator chat IDs are `sim-<phone>`.
- **Menu search** is a fuzzy scan in Python over names, categories and aliases, which is fine for a menu.
