# Mitu: bilingual AI customer service agent for WhatsApp (tech demo)

Mitu answers customers of **Shuta Fashion**, a demo Dhaka clothing shop. It replies in Bangla, English or Banglish (whatever the customer used), answers product, delivery and policy questions, takes orders, checks order status, understands voice notes and photos (product screenshots, bKash receipts), and hands off to a human when needed.

- **Chat simulator** (`#/simulator`, signed-in staff only, since every message runs the paid LLM): a WhatsApp-style phone screen in the browser, with a hold-to-record mic and a photo button.
- **Real WhatsApp**: through the official WhatsApp Cloud API webhook.
- **Dashboard** (home page, password login): sales analytics (revenue per day, top products, payment and delivery mix, order status, AI-handled share, each compared with the previous period), live chats with human replies and a bot on/off switch per chat (under each customer message, "AI used …" opens the tools the AI called, what they returned, and what that turn cost), and orders.

Both channels run the same code path: `pipeline.py` → `agent.respond()` → LLM with tools.

```
business.yaml          all shop details and products (swap this to change business)
backend/
  app/config.py        .env + business.yaml
  app/db.py            SQLAlchemy models (SQLite by default)
  app/tools.py         search_products, get_product, send_product_photo, saved_details, create_order, record_payment, get_order_status, cancel_order, get_business_info, escalate_to_human
  app/llm.py           LLM interface: Bedrock Converse or the Anthropic API (LLM_PROVIDER)
  app/agent.py         system prompt, history, tool loop, handoff
  app/pipeline.py      one entry point per message type (text / voice / other), used by both channels
  app/whatsapp.py      webhook (verify, signature, dedupe) + Graph API client
  app/voice.py         SpeechToText / TextToSpeech interfaces, OpenAI + Amazon Transcribe, ffmpeg helpers
  app/main.py          FastAPI: simulator + dashboard API
  seed.py  cli.py  compare_stt.py  tests/
frontend/              React + Vite + Tailwind (simulator + dashboard)
```

## 1. Run locally

You need Python 3.12+, Node 20+ and **ffmpeg** on your PATH (ffmpeg is only needed for voice notes).

```bash
cp .env.example .env           # then fill in at least the LLM credentials and ADMIN_PASSWORD

# backend
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python seed.py                         # loads products from business.yaml + 3 sample orders
                                                 # (add --demo for 60 days of fake orders to fill the analytics page)
.venv/bin/python cli.py 01712345678              # optional: chat with the agent in the terminal
.venv/bin/uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                                      # http://localhost:5173
```

Open http://localhost:5173. The dashboard is the home page and asks for `ADMIN_PASSWORD` (the server refuses to start while it's empty, `admin` or `change-me`). The simulator is at `#/simulator` once you're signed in.

Run the tests (no credentials needed, the LLM and Graph API are mocked):

```bash
cd backend && .venv/bin/python -m pytest
```

The tests use a fake LLM, so they can't tell you whether the real model behaves. The eval does: 31 scripted customer conversations in `backend/evals/cases.yaml` (ordering, stock, cancelling, refunds, handoff, prompt injection, …) run against the real model from `.env`, each on a throwaway database, checking which tools were called, what ended up in the database, and the replies. Run it after changing the prompt, the tools or the model. It costs roughly $1–3 per full run on Sonnet (about double on Opus, free on Ollama):

```bash
cd backend && .venv/bin/python eval.py              # all cases; exit code 1 if any fail
.venv/bin/python eval.py cancel refund              # only cases whose name contains a word
```

Models vary from run to run, so a single failure is worth reading (the transcript is printed) before you change anything. Add a case whenever a real chat goes wrong.

**Docker:** `cp .env.example .env`, fill it in, then `docker compose up --build` and open http://localhost. Caddy serves the app and passes `/api`, `/media` and the webhooks to the backend, which has no public port. Seed data loads on first start, and the database and voice files live in the `data` volume. A `backup` container writes a `.tar.gz` of the database and media to `./backups` at start and then daily, keeping 14.

### LLM settings

- `LLM_PROVIDER=bedrock` (default) uses boto3 `bedrock-runtime` `converse` with tool use, with the model from `BEDROCK_MODEL_ID` and the standard AWS credentials. Enable Claude model access in the Bedrock console first. If your region needs an inference profile, use the prefixed ID (e.g. `us.anthropic.claude-opus-5-5`).
- `LLM_PROVIDER=ollama` runs a local model through [Ollama](https://ollama.com), with no cloud account: `ollama pull qwen3:4b`, then set `OLLAMA_MODEL`. It needs a model with tool support. It's fine for trying the flow, but expect weaker Bangla, more mistakes, and slow replies on a CPU.
- `LLM_PROVIDER=anthropic` uses the Anthropic API with `ANTHROPIC_API_KEY`. It runs at `LLM_EFFORT=low` for snappy chat replies, and server-side refusal fallbacks are on (`fallbacks: "default"`), so a false-positive safety decline is retried on another model instead of failing.

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

For anything beyond a demo, create a **System User** in Business Settings and generate a permanent token instead of the 24-hour one. A real business number also needs Meta business verification.

## 3. Use a different business

Edit `business.yaml` (or point `BUSINESS_FILE` at another file): name, agent name, hours, location, delivery areas and charges, payment methods, return policy, the fixed fallback messages, and the products. Delivery-area and payment keys feed straight into the tool schemas, so you can add, say, `chattogram_city: {label: ..., charge: 100, time: ...}` without touching code.

For products, give each one an `id`, both names, a `category`, `aliases` (the spellings customers type, e.g. Banglish), price, sizes, colours and stock. Then run `python seed.py` to load them. Note that this resets stock to the numbers in the file.

## 4. CI/CD and deploying

`.github/workflows/ci-cd.yml` runs on every push and pull request:

1. **Backend tests** (`pytest`, fake LLM and mocked Meta API, so no secrets needed) and **frontend build**.
2. On `main` only: builds the backend and frontend Docker images and pushes them to GitHub Container Registry
   (`ghcr.io/<owner>/<repo>-backend` / `-frontend`, tagged `latest` and the commit SHA).
3. Deploys them over SSH with `docker compose pull && up -d`, once these repository secrets exist
   (Settings → Secrets and variables → Actions):

| Secret | What |
|---|---|
| `ENV_FILE` | The whole production `.env` (API keys, `ADMIN_PASSWORD`, `STAFF`, WhatsApp/Page tokens, `PUBLIC_URL`) |
| `DEPLOY_HOST` | Server address; without it the deploy step is skipped |
| `DEPLOY_USER` | SSH user that can run `docker` |
| `DEPLOY_SSH_KEY` | That user's private key |

Update the production settings with `gh secret set ENV_FILE < .env.production` and re-run the workflow.
On the server, set `SITE_ADDRESS` in `ENV_FILE` to your domain and point the domain's DNS at the server with ports 80 and 443 open: Caddy gets and renews the HTTPS certificate itself. Your webhook URLs are then `https://<domain>/webhook` and `https://<domain>/messenger/webhook`.

Backups land in `~/mituai/backups` on the server. **Copy them off the server** (e.g. a nightly `rclone copy` or `aws s3 sync` from cron): a backup on the same disk doesn't survive losing the server. To restore, stop the stack, put `shop.db` and `media/` from an archive back into the `data` volume, and start it again.

## Voice notes

The flow: WhatsApp `audio` message → `GET /{media_id}` → download with the token → save the original (for the dashboard player) → ffmpeg to 16 kHz mono WAV → reject anything over 2 minutes → speech-to-text → if the transcript is empty or confidence is below `STT_MIN_CONFIDENCE`, reply "please repeat or type it" (never guess) → otherwise the same `agent.respond()` as typed text. The transcript is stored marked as a voice note, and each step's time is logged.

- `STT_PROVIDER=openai`: OpenAI `whisper-1`, with a prompt hinting at shop vocabulary. Confidence comes from segment log-probabilities.
- `STT_PROVIDER=aws`: Amazon Transcribe batch. It uploads to `TRANSCRIBE_S3_BUCKET`, runs a job with language identification over `bn-IN` and `en-US`, polls, then deletes the object and the job.
- To compare both on the same recordings, run `python compare_stt.py backend/media/*.ogg`. It prints text, detected language, confidence and latency per provider.
- `VOICE_REPLY=true` also sends a spoken reply (OpenAI TTS → ffmpeg to OGG/Opus → `POST /{phone_number_id}/media` → audio message). The text version is always sent too.

## Decisions

Simplest options picked where the brief was open:

- **Order confirmation is enforced in code, not just the prompt.** `create_order(confirmed=false)` validates and quotes, and stores the quote on the chat. `confirmed=true` only works if that quote was made in an *earlier* turn, so the customer must have sent a message after seeing the summary. Whether that message means "yes" is the LLM's call.
- **History is text only**, except the customer's own photos (JPG/PNG), which are re-sent as images every turn (prompt caching makes the repeats cheap). Each turn's tool calls stay inside that turn, and the model re-looks things up next turn (the prompt tells it to). The last 30 messages are sent. Photos need a vision model, so with Ollama use one like `qwen2.5vl`.
- **Stock is per size and colour.** Cancelling an order returns its stock, whether the owner cancels it in the dashboard or the customer does in chat. Customers can only cancel their own orders from the same chat, before shipping, and not once they've paid (a refund needs a human).
- **Language label** in the dashboard comes from a cheap heuristic (Bangla script, or common Banglish words). The LLM mirrors the customer's language on its own.
- **Handoff** sets `needs_human` and switches the bot off for that chat. Turning the bot back on in the dashboard clears the flag. If the LLM call fails, the customer gets a polite fixed message and the chat is flagged.
- **Fixed replies** (unsupported media, unclear or too-long voice note, errors) skip the LLM and come from `business.yaml`. They are bilingual because the language may not be known yet.
- **`% handled fully by AI`** counts today's chats that were never flagged and got no human reply.
- **"Today"** uses `timezone` from `business.yaml` (Asia/Dhaka).
- **Live updates** use polling (1–5 s), not websockets.
- **Dashboard auth** is one password per person in `.env`, sent as a header (so HTTPS matters). 10 wrong passwords from one IP lock that IP out for 15 minutes; per IP, so a guesser can't lock the owner out. The lockout is in memory and resets on restart. Voice files under `/media` are public but have random 128-bit names.
- **The simulator** runs in background tasks like WhatsApp and the page polls, so it exercises the exact same path. Simulator chat IDs are `sim-<phone>`.
- **TTS** has one provider (OpenAI) because Amazon Polly has no Bangla voice.
- **Product search** is a fuzzy scan in Python over names, Bangla names and aliases, which is fine for a small catalogue.
