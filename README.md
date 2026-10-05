# Munim AI: a voice-first business copilot for Paytm kirana merchants

A shopkeeper asks out loud, in Hindi, Hinglish or Marathi, *"Aaj ka hisaab kaisa raha?"*, and Munim AI speaks back with the numbers plus one insight that matters: the weekly Tuesday dip, regulars who stopped coming, or overdue udhaar. He can update his udhaar khata by voice (*"Suresh ne 500 rupaye diye"*) and send payment reminders. Nothing is saved or sent until he says "haan".

**Everything is real and runs on your machine, on free services:** a real AI, real speech, a real database and real Telegram messages. Only the store data is synthetic (realistic, generated).

```
Chrome/Edge (free speech in & out) ──► FastAPI (local) ──► agent ──► Groq free tier (3 models, automatic fallback)
     login: phone + PIN                    │                 │
                                           │                 └─► tools ──► SQLite (merchant-scoped)
                                           └─ logs, metrics, rate limits      └─► Telegram Bot API (reminders, free)
```

| Need | What runs (real, free) |
|---|---|
| AI brain | Groq free tier: gpt-oss-120b, falling back to gpt-oss-20b and qwen on rate limits (Gemini or Claude also supported) |
| Speech in and out | Chrome/Edge built-in speech recognition + Hindi voice |
| Database | SQLite, a single local file at `data/munim.db` |
| Reminders | Telegram Bot API |
| Login | Real SMS OTP to the merchant's phone (Message Central VerifyNow) |

There is no mock or simulated mode in the app. If a service is missing or down, Munim says so honestly; it never pretends.

---

## 1. Run it

```powershell
cd munim-ai
pip install -r requirements.txt          # add  -r requirements-dev.txt  to run the tests
copy .env.example .env          # add GROQ_API_KEY, Message Central OTP keys, Telegram bot (see below)
python -m app.cli --check-llm         # real AI + a real tool call
python -m app.cli --check-otp         # real OTP SMS to your phone, then verify it
python -m app.cli --check-telegram    # real test message on your phone
python -m app.serve
```
The first start creates the database and loads the demo store for the phone in `SEED_MERCHANT_PHONE`.
Open **http://127.0.0.1:8000** in **Chrome or Edge**, enter that phone number, tap **OTP भेजें**, type the SMS code, allow the microphone, tap 🎤 and speak.

**Developer mode** (`DEV_MODE=true` in `.env`) skips the phone and OTP step and logs you in as the demo store automatically. It only works on this computer (`HOST=127.0.0.1`), the server refuses to start with it on any other host, and the top bar shows a "डेवलपर मोड" badge. Set it to `false` to test the real OTP login.

**If the page shows "सर्वर पुराना चल रहा है"**, the server process is older than the page files. Stop it (Ctrl+C) and run `python -m app.serve` again.

**OTP setup (Message Central, free signup credits, no DLT needed for OTP).** Sign up at https://www.messagecentral.com and open *Verify Now*. Copy your **Customer ID** into `MESSAGECENTRAL_CUSTOMER_ID` and your account password into `MESSAGECENTRAL_PASSWORD` (plus `MESSAGECENTRAL_EMAIL`). Then run `python -m app.cli --check-otp`: a real SMS arrives, you type the code, and it is verified.

**Telegram setup (2 minutes, free).** In Telegram, open **@BotFather** and send `/newbot`, then copy the token into `TELEGRAM_BOT_TOKEN`. Send any message to your new bot. Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`. Without it, the app works, but reminders reply "Telegram abhi set up nahi hai".

The server refuses to start without an AI key, and warns at startup if Telegram is missing.

Change the login phone with `python -m app.manage set-phone --phone <10 digits>`. To start over from clean demo data, run `python -m app.manage reset-demo`.

## 2. What it can do

**Proactive dashboard (opens after login, no question needed).** All of it is computed from the data in code, with no AI call, so it's instant and exact:
- **Aaj ki briefing:** a one-paragraph summary of the day plus the 3 most important things. Tap *Briefing suno* to hear it.
- **KPI tiles:** today's sales (vs the same weekday, with a 14-day sparkline), UPI payments, udhaar outstanding, and weekly business at risk.
- **Ranked insight cards with rupee impact and a one-tap action:** lapsed regulars (₹4,012/week at risk), overdue udhaar, the Tuesday dip (+₹4,234/week if fixed), a grocery drop linked to the lapsed regulars, slow stock, peak hours, and the top customer.
- **Charts:** 28-day sales with Tuesdays highlighted, the weekday pattern, and category share.
- **Udhaar khata:** overdue badges and a *Reminder* button on every customer.

**UPI repayment matching.** Udhaar never shows up in UPI data (no money changes hands), but its *repayment* often does: a customer who owes udhaar sends money by UPI. When a UPI payment without a bill arrives from a customer who owes udhaar, matched by UPI ID or name, Munim asks instead of guessing:
> 💰 राजेश गुप्ता ने ₹1,000 UPI से भेजे · इनका ₹2,400 उधार बाकी है → **हाँ, उधार में जमा करें** / **नहीं, यह खरीदारी थी**

- **हाँ:** the khata goes down by the payment, recorded as a UPI entry in the ledger and audit log.
- **नहीं:** it counts as a sale; the khata is untouched.
- **Payment bigger than the debt:** it clears the debt and the rest counts as a sale.
- **Telegram:** the shopkeeper gets a message when such a payment arrives.
- **Voice:** "राजेश के ₹1,000 उधार में जमा कर दो" works too (preview, then "हाँ").
- **Safety:** a payment can be decided only once, even with 20 simultaneous clicks, and duplicate webhooks are ignored.
- **Where payments come from:** in production, the payment provider calls `POST /api/webhooks/upi` (header `X-Webhook-Secret: $UPI_WEBHOOK_SECRET`; idempotent on `txn_id`). In the demo, use the **🧪 डेमो: UPI पेमेंट आने दें** button (developer mode) or `python -m app.manage upi-payment --customer Suresh --amount 500`. The demo store starts with one waiting suggestion: Rajesh, ₹1,000.

**Voice assistant (right panel).** Answers come with rich cards (stats, mini charts, customer lists, reminder previews, ledger entries), and the dashboard refreshes after every change. **नई बातचीत** starts a fresh chat without deleting the old one; **🕘 पिछली बातचीत** lists earlier chats (first question, time, number of questions), and any of them can be reopened and continued.

| Merchant says | Tool | Effect |
|---|---|---|
| Aaj ka hisaab kaisa raha? | `daily_summary` | Sales, payment count, comparison with the usual, and one proactive insight |
| Mangalvaar ko dhanda kaisa gaya? | `daily_summary("mangalvaar")` | ₹7,437; Tuesday is always ~33% lower, so it suggests an offer |
| Suresh ka kitna udhaar baaki hai? | `udhaar_lookup` | ₹1,850, last paid 19 days ago (overdue) |
| Kaun se purane customer aana band ho gaye? | `lapsed_customers` | Anita, Vikram, Kavita; about ₹4,000/week at risk |
| Kya cheez nahi bik rahi? | `product_performance` | Household ~5% of sales; Harpic barely moving |
| Suresh ko reminder bhej do → **Haan** | `send_reminder` | Preview first, then sent to Telegram after "haan" |
| Suresh ne 500 rupaye diye → **Haan, likh do** | `record_udhaar` | Ledger updated: ₹1,850 → ₹1,350 |
| Ramu ko 300 ka saman udhaar diya → **Haan** | `record_udhaar` | Opens a khata for a new customer |

**Language switch: हिंदी · English · मराठी.** A menu in the top bar (and on the login screen) switches the whole app: headings, buttons, KPI tiles, insight cards, the briefing, chart labels and legends, the khata, suggestion chips, error messages, the assistant's answers and their cards, the microphone language and the voice. In English, customer names appear in Latin script ("Suresh"); in Hindi and Marathi they appear in Devanagari ("सुरेश"). The choice is remembered on that device. Answers follow the chosen language too: if the AI slips into Hindi on the English screen, the code asks it again for English. The voice is picked by the script the text is written in, so Hindi text is never given to the English voice (that made it sound garbled). Hindi is the default, and you can always type or speak Hinglish ("Suresh ka udhaar kitna hai?"). The reminder message the *customer* receives stays in Hindi. The page is marked "do not translate", so Chrome's auto-translate can't mangle it.

**Specific days and people.** Ask about any day the way you'd say it: "15 सितंबर", "15 September", "15/9", "2 tarikh", "3 din pehle", "parso", "पिछले मंगलवार", "काल" (Marathi). A day with no records gets "records cover 4 August to 2 October" instead of made-up numbers, and a future date is refused. People are found with or without honorifics ("Suresh ji", "सुरेश"). For a misspelt name ("Sursh") Munim asks *"Did you mean Suresh?"* and changes nothing. It never records a payment to, or opens a second khata for, a near-match. Amounts like "₹1,000", "500 rupees" or "५००" are understood.

**Voice.** Answers and the briefing are spoken by Microsoft's free neural voices (Hindi स्वरा / मधुर, Marathi आरोही / मनोहर, English नीरजा / प्रभात) through `edge-tts`. Every answer has a **🔊 सुनें** button to hear it again, including answers restored after a reload. The audio player unlocks on your first tap, so Chrome's autoplay rule can't silently block answers. If it still blocks one, the 🔊 button pulses. If the neural voice is unavailable, the browser's voice is used, but *only* if it speaks the right language. An English voice reading Hindi would skip the words and read just the numbers; instead you get a short note to read the answer on screen. Choose the voice in the assistant panel; set `VOICE_ENGINE=browser` to always use the browser voice. If the server is updated while a tab is open, the tab shows "please refresh".

**Look.** A Paytm-style theme: navy top bar with a sky-blue line, navy buttons, cool white (or navy, in dark mode) background. It uses Paytm's colours only, not its logo, so it doesn't pose as an official Paytm app.

**When the free AI is busy.** Groq's free models each have a daily and per-minute limit. Munim tries them in turn (gpt-oss-120b, gpt-oss-20b, Qwen, gpt-oss-safeguard-20b). If all are busy and `GEMINI_API_KEY` is set, it switches to Google Gemini (2.5 Flash, then Flash-Lite), which has separate free limits. If the data was already fetched but no AI can word the answer, Munim states the key facts itself ("Total udhaar is ₹8,700 across 6 customers…") instead of an apology.

**Confirming actions.** A clear, short "yes" (yes / ok / haan / हाँ / हो / send it) to the preview shown in the previous turn always confirms that exact action, even if the AI hesitates. Anything with "no", a different name or a new question confirms nothing. If the action succeeds but the AI can't word the reply (e.g. a rate limit), Munim still says plainly that it's done.

**Colours.** Every chart says what its colours mean: orange = the problem (the slowest day, the weakest category), blue = the day being discussed or today, grey = everything else. Insight cards use red = ज़रूरी, yellow = ध्यान दें, green = मौका, blue = जानकारी, always with the word next to the colour.

## 3. Production features

**Data and correctness**
- **SQLite system of record.** Merchants, customers, products, transactions, an **append-only udhaar ledger**, reminders, conversations and an **audit log**. WAL mode, foreign keys, CHECK constraints, versioned schema.
- **Numbers never come from the AI.** Every figure is computed in code from the database; the AI only phrases the answer.
- **Multiple merchants, isolated data.** The merchant comes from the login session, never from AI tool arguments. Tests prove one merchant can't see another's data.

**Safety**
- **Two-turn confirmation** for anything that writes data or contacts a customer. The agent enforces it in code, not just in the prompt: a confirmation is accepted only for the *exact* action previewed, and only in the *very next* turn.
- **Reminder rules.** Per-customer opt-out, a 72-hour cooldown, and a full history.
- **Validation.** Payments can't exceed what's owed, amounts are capped, and every ledger write is audit-logged.

**Security**
- **Real SMS OTP login** (Message Central). The code is generated, delivered and checked by Message Central; we store only its verification ID, never the code. Unknown numbers get the same answer but no SMS (no account discovery, no wasted credits). Limits: 60 s resend cooldown, 3 OTPs per 10 min, 5-minute OTP expiry, 5 wrong tries locks the number. Optional PIN login (`LOGIN_METHOD=pin`, scrypt-hashed PINs).
- Session tokens are random 256-bit values; only their SHA-256 is stored. Cookies are HttpOnly and SameSite=Strict and expire after 12 h. API clients can use a Bearer token.
- A strict **Content-Security-Policy** (no inline scripts), X-Frame-Options, nosniff, Referrer-Policy, and no-store on API responses.
- **Rate limits** on login (per IP) and questions (per merchant, which also protects the free AI quota). Input size limits. Errors never leak internals.
- Binds to **127.0.0.1** by default.

**Reliability**
- **Model fallback chain:** gpt-oss-120b → gpt-oss-20b → qwen. Groq limits each model separately, so this roughly triples the free capacity. Short rate limits are waited out automatically.
- **Free-model quirks handled:** leaked reasoning ("We have to follow rules…") is detected, retried and cleaned; echoed sentences are removed; older turns are compacted so requests stay inside free-tier token limits.
- **Honest failure:** if the AI is unreachable, the merchant hears "Maaf kijiye, abhi AI se jud nahi paaya…". A failed Telegram send is reported and recorded, never shown as sent.
- **Conversations persist** across restarts and page refreshes.
- A failed turn never corrupts the history.

**Observability**
- JSON-lines logs in `logs/munim.log` (rotated), with a request ID and merchant ID on every line. Every response carries an `X-Request-ID` header.
- `GET /health` (liveness), `GET /ready` (database + AI), and `GET /api/admin/metrics` (request counts, latency, tool usage, AI failures; needs `ADMIN_TOKEN`).

## 4. Testing

Three layers. Unit tests check the parts in isolation, functional tests check the whole app the way a person uses it, and stress tests check it under many users at once.

```powershell
python -m pytest -q                                   # 274 unit/integration tests incl. edge cases (fresh database per test)
python -m pytest tests_e2e/test_functional.py -q      # 38 functional tests: real server + real Chrome
python -m pytest tests_e2e/test_stress.py -q -s       # 9 stress/concurrency tests, prints a report
$env:E2E_REAL_AI="1"; python -m pytest tests_e2e -q -k real_ai; Remove-Item Env:E2E_REAL_AI   # real Groq AI in the browser
python -m evals.run_evals                             # 34-case accuracy eval on the real AI (uses lots of quota)
```

**Functional tests** start the real server process on a throwaway database copy and drive your installed Chrome with Playwright. They click and type exactly like a user would and cover:
- developer-mode and OTP login (wrong code, then right code; unregistered number; logout)
- dashboard sections, charts and legends, chart tooltips, khata with Devanagari names
- recording a payment by voice/text (preview, then "हाँ", then the khata updates); declining a payment
- a reminder from the khata button (preview, confirm, the Telegram message, cooldown)
- insight action buttons, suggestion chips and rich answer cards
- the briefing played with the real neural voice; the male voice option
- conversation surviving a page reload and a server restart
- error card with retry, the outdated-server banner, the AI-down message
- switching to English and Marathi changes every visible word, survives a reload, and questions/voice follow the language; the login screen too
- a specific date ("15 सितंबर" → ₹8,921), a date with no data, a misspelt name ("did you mean?", nothing saved), a specific person
- voice under Chrome's *normal* autoplay rules: the neural voice plays; with no Hindi browser voice, nothing is read as bare numbers
- mobile layout (no sideways scrolling); dark and light mode
- XSS in a customer name, security headers, path traversal, oversized requests, SQL-injection text, prompt injection

Only two things are replaced, so tests never contact real people: the OTP check (accepts 246810, sends no SMS) and Telegram (messages are written to a file the tests read). The AI is the deterministic test brain, except in the opt-in real-AI test.

**Edge cases (`tests/test_edge_cases.py`, 135 tests).** These cover 43 ways of saying a day and 6 nonsense dates (each must give a clear message, never a crash), future dates and dates outside the data, plus name variants, typos, unknown people, empty or markup names and an honorific-only name ("ji"). Amounts include 0, negative, too large, infinity, NaN, "₹1,000", "५००", "paanch sau", an exact full repayment and ₹1 over. Day counts that are 0, negative, huge or text are clamped to a sensible range. Every check also runs in all three languages. The sweep found and fixed several bugs:
- dates like "15 September" or "2 tarikh" weren't understood
- "Suresh Yadav" matched two people
- the name "ji" opened a khata called "Ji"
- "₹500" was rejected
- an infinite amount crashed the tool
- a typo gave no suggestion

The 11 new real-AI eval cases (specific dates, specific and unknown people, a typo, an overpayment, English and Marathi) all pass on Groq.

**Stress results (last run):**

| Scenario | Result |
|---|---|
| 20 users × 5 questions in parallel | 100/100 OK · p50 140 ms · p95 654 ms |
| 25 simultaneous udhaar entries, same customer | 25/25 saved · no lost writes · 25 audit rows |
| 30 simultaneous ₹100 payments on a ₹640 balance | exactly 6 saved, 24 refused · balance ₹40 (never negative) |
| 10 simultaneous "हाँ, भेज दो" for the same reminder | exactly 1 Telegram message · 9 blocked |
| 300 dashboard loads, 30 parallel | 142 req/s · p50 96 ms · 0 errors |
| 8 parallel neural-voice requests | 8/8 OK · ~1.4 s each |
| Rate limit 10/min, 25 parallel questions | exactly 10 allowed, 15 refused |
| 20 simultaneous "हाँ" clicks on one UPI payment | applied once, 19 refused |
| Same webhook delivered 20 times at once | stored once · 0 server errors |
| After all of it | ready · 0 server errors · 0 agent errors |

The stress tests found and fixed two real race conditions: simultaneous payments could overdraw a khata, and simultaneous confirmations could send a reminder twice. Both are now handled inside one locked database transaction.

## 5. Admin CLI

```powershell
python -m app.manage list-merchants
python -m app.manage create-merchant --id patil-kirana --store "Patil Kirana" --owner Sunita --phone 9822000002
python -m app.manage set-phone --phone 9876543210
python -m app.manage ledger --merchant sharma-gs
python -m app.manage opt-out --merchant sharma-gs --customer Suresh      # --undo to re-enable
python -m app.manage audit --merchant sharma-gs
python -m app.manage reset-demo                                          # fresh demo data
python -m app.cli --demo                                                 # scripted demo in the terminal
python -m app.cli --check-llm | --check-otp | --check-telegram           # integration checks
```

## 6. Configuration

All settings are in `.env`; see `.env.example` for every option with comments. The key ones:

| Setting | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | (none) | free key |
| `LLM_PROVIDER` | groq | groq / gemini / anthropic |
| `MESSAGECENTRAL_CUSTOMER_ID`, `MESSAGECENTRAL_PASSWORD` | (none) | required for OTP login; the server won't start without them |
| `SEED_MERCHANT_PHONE` | (none) | the phone that logs in to the demo store |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | (none) | without them, reminders are refused with a clear message |
| `REMINDER_COOLDOWN_HOURS` | 72 | |
| `HOST` / `PORT` | 127.0.0.1 / 8000 | |
| `ADMIN_TOKEN` | (none) | enables the metrics endpoint |
| `UPI_WEBHOOK_SECRET` | (none) | enables the UPI payment webhook |
| `DEV_MODE` | false | skip phone/OTP login on this computer |

Before a live demo, run `python -m app.manage reset-demo`. Rehearsal reminders sent through Telegram start the 72-hour cooldown, which would block the same reminder on stage.

## 7. Project layout
```
app/
  serve.py            one-command start (DB init + demo seed + server)
  main.py             API: auth, ask, khata, conversation, health/ready/metrics, security middleware
  auth.py             SMS OTP login (+ optional PIN), sessions, lockout, OTP limits
  db/database.py      SQLite schema, connections, transactions, migrations
  db/store.py         merchant-scoped data access, ledger, reminders, audit, conversation persistence
  db/seed.py          loads the demo dataset into SQLite
  agent/agent.py      tool loop, confirmation guard, persistence
  agent/tools.py      the 6 tools (numbers computed from the DB)
  agent/insights.py   proactive insight engine: KPIs, ranked insights with ₹ impact, briefing
  agent/system_prompt.py
  services/           Groq/Gemini adapter (fallback, leak guard), Claude, Telegram, Message Central OTP
  observability.py    JSON logs, request ids, metrics
  ratelimit.py        sliding-window limiter
  hindi.py            Devanagari names, weekdays, months, categories
  payments.py         UPI repayment matching (suggest, link, dismiss, webhook)
  services/tts_service.py  neural voice (edge-tts)
  manage.py / cli.py  admin + terminal tools
  web/                index.html + static/app.js + static/app.css
data/                 demo dataset generator + data/munim.db (created on first start)
evals/                accuracy cases + runner
tests/                274 unit/integration tests (test doubles for AI / OTP / Telegram live only in tests/)
tests_e2e/            functional (Playwright + real server) and stress tests
```

## 8. Known limits
- **Synthetic data:** one demo store, 60 days, ending 2 Oct 2026. "Today" means the last day in the data (`TODAY_MODE=data`).
- **Free AI tier:** about 8k tokens/minute and **200k tokens/day per model** (3 models). One question uses about 5k tokens, so roughly 100+ questions a day across the models. Don't run the real-model evals on demo day; they use a large share of the daily budget. The dashboard, briefing and insights use no AI tokens at all.
- **Fresh-data guard:** if the AI quotes numbers without fetching data in that turn (for example, copying an earlier, now-stale answer), the draft is discarded and the AI is made to call a tool.
- **Speech needs Chrome/Edge** and internet, because Google's speech service does the recognition.
- **One process, local:** rate limits and metrics are in memory. Moving to a server would need HTTPS (`COOKIE_SECURE=true`), a shared rate-limit store and a process manager.
