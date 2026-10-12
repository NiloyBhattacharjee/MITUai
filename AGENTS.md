# Agent guide

Mitu: an AI assistant for cafés on WhatsApp, Messenger and Instagram. One install runs many cafés. Read `README.md` for
the layout and the **Decisions** section before changing behaviour; don't redo a decision without saying why.

## Commands

```bash
cd backend && .venv/Scripts/python -m pytest -q   # ~15 s, no credentials (fake LLM, mocked Meta API). Linux/mac: .venv/bin/python
cd frontend && npm run build                       # the only frontend check CI runs
cd backend && .venv/Scripts/python eval.py <word>  # real LLM, costs money: only when asked, or after prompt/tool/model changes
```

CI (`.github/workflows/ci-cd.yml`) runs exactly the first two. Run them before calling a change done, and show the output.

## Rules that protect real data

- **Tenancy:** every café-owned table subclasses `db.Scoped`; `app/tenancy.py` filters every query to the café in
  scope and raises with none. Never add a manual `shop_id` filter as a workaround, never query a `Scoped` table outside
  `tenancy.scope(...)`. New café tables get a test in `tests/test_tenancy.py` proving one café can't see another's.
- **Money is integer cents.** Convert with `config.cents()`, display with `config.money()`.
- **Time is the café's timezone** via `hours.local_now()`, never `datetime.now()`. Tests pin the clock to 10:00.
- **Order confirmation is enforced in code** (`create_order`), not only in the prompt. Keep it that way.
- Don't touch `.env`, `backend/shop.db`, `backend/media/` or `backups/`. Secrets and data never go in commits.
- Every channel goes through `pipeline.py` → `agent.respond()`. Fix shared behaviour there, not per channel.

## How to work

- Read the code the change touches and grep every caller before editing. Fix root causes in the shared function.
- Smallest change that works. Reuse what's here; no new dependency for what a few lines do.
- Bug fix or new rule → a failing test first, in the matching `tests/test_*.py`, then the fix.
- A real chat that went wrong → add a case to `backend/evals/cases.yaml`.
- Prompt, tool schema or LLM-provider changes (`agent.py`, `tools.py`, `llm.py`): say the eval should be run; don't
  run it unprompted.
- Update `README.md` when setup, env vars or a Decision changes. New env vars go in `.env.example` too.
- Stuck after two attempts at the same failure: stop and report what you tried instead of guessing further.

## Git

- One branch per change, off `main`. Commit and push the branch to `origin` when done. Merge into `main` only when asked; never force-push.
- Commit messages: imperative subject saying what changed for the café or developer, body only if the why isn't obvious.
- Never skip hooks or commit with failing tests.
