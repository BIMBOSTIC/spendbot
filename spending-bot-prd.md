# Daily Spend — Product & Build Spec (v3)

**Owner:** READOX
**Timeline:** 2 weeks (solo build, AI-assisted via Cursor/Claude Code)
**Platform:** Telegram bot (MVP) → WhatsApp later if it gains traction
**Companion:** an in-chat prototype of the conversation flow was built and reviewed before this spec — use it as the reference for tone and interaction, not a literal UI copy.

---

## 1. Problem

People don't lose money on rent or big purchases — they lose it on untracked daily small spend
(bread, transport, "gave X money"). The friction of opening a budgeting app kills the habit
within days. A chat-based logger removes that friction: you're already in Telegram/WhatsApp,
so logging a spend is as fast as sending a text.

## 2. Competitive landscape (as of mid-2026)

| Product | Channel | Core mechanic | Gap |
|---|---|---|---|
| POQT | WhatsApp | NLP logging, budgets, financial health score, family sharing | Generic categories, no relational/lend tracking, no item-level price history |
| Whispend | WhatsApp | Voice notes + receipt OCR | Same generic category model |
| Tyms AI | WhatsApp | Bookkeeping for small businesses, auto invoices | B2B focus, not personal daily spend |
| Zyno (Elitemindz) | WhatsApp | Approval workflow, reimbursement dashboard | Enterprise expense management, not personal habit tool |
| Open-source Twilio+Sheets bots | WhatsApp | Regex parsing into a spreadsheet | No AI, single-expense-per-message only |

**What none of them do well:**
- Track money **given to specific people** as its own category with per-person running totals
- Split **one message into multiple itemized line items**
- Treat **borrowing/lending** as a real ledger, not a lump "expense"
- Track **individual item price history** over time — "how much did bread cost me 6 months ago vs now"
- Let categories evolve with the user instead of locking them into a fixed set

## 3. Differentiation strategy (your edge)

1. **Item-level price memory (the headline feature)** — every line item is stored not just under
   a category, but as a named item ("bread", "milk", "transport"). This means the bot can answer
   things no competitor above can: "how much did bread cost me last 6 months vs now", "what's
   gone up the most this year", "show me my kebab spend over time". This turns raw logging into
   a personal inflation tracker — genuinely new, not a copy of existing NLP-logging bots.
2. **Relational ledger** — `gave pedro 500` creates a person-tagged entry. `/gave-summary` shows
   running totals per person, lifetime and this month.
3. **Multi-item parsing** — one message like "bread 100, milk 88, moucha 50" gets split into line
   items automatically, matching how people actually write shopping notes.
4. **Borrow ledger** — separate from expenses. `/borrow 2000 from mum` and `/repaid 500 to mum`
   track a running balance, not a one-off transaction.
5. **Flexible categories** — start with sensible defaults (Food, Transport, Bills, Gave, Borrow,
   Others) but let the bot learn new categories from usage instead of locking users into a fixed
   list. If someone logs "gym membership" three times, the bot offers to make it its own category.
6. **Summary on demand, not just on schedule** — most bots push you a digest and stop there.
   This bot answers spending questions the moment you ask, in plain language ("how much have I
   spent this week", "how much on transport in May") — no need to wait for a weekly digest or
   dig through a dashboard.
7. **Zero cost to start** — Telegram Bot API (free, no approval) instead of WhatsApp Business API
   (paid, needs Meta Business verification).
8. **(Phase 2) Wallet Trade Tracker** — paste a wallet address, bot monitors on-chain trades
   and delivers a daily P&L report, win rate, and loss totals on demand. Read-only, no key needed.
   Spec in Section 11.
9. **(Phase 2) Savings Tracker** — daily savings goal + habit log. Bot surfaces streaks, missed
   days, and cumulative totals. First-time setup is a single question. Spec in Section 12.

## 4. Explicitly OUT of scope for the 2-week MVP

- Automatic bank/credit-card transaction capture (needs Plaid/Mono/Okra partnership + compliance
  — months-long track, not a bolt-on feature)
- Receipt photo OCR
- WhatsApp channel (Phase 2 — Meta Business verification + per-conversation billing)
- Multi-currency conversion
- Full web dashboard (a simple read-only chart view is fine; a full app is not)

## 5. Data model

The core shift from v1: items are first-class, not just notes text. This is what makes price
history queries possible.

```
User
  id, telegram_id, display_name, currency (default TRY), timezone, created_at

Category
  id, user_id (nullable — null = system default), name, is_default
  Defaults: FOOD, TRANSPORT, BILLS, CLOTH, GAVE, BORROW, OTHERS
  Users can implicitly grow this list; the bot suggests promoting a repeated
  "OTHERS" note into its own category after ~3 occurrences.

Item
  id, user_id, canonical_name (e.g. "bread"), category_id
  This is the entity that price-history queries run against. Free-text like
  "bread", "loaf of bread", "sliced bread" should normalize to one canonical_name
  — simple string normalization for MVP, fuzzy match is a Phase 2 refinement.

ExpenseEntry
  id, user_id, raw_message, category_id, total_amount, created_at

LineItem
  id, expense_entry_id, item_id, amount, quantity (nullable), created_at
  This table is what "bread price last 6 months" queries against:
  SELECT amount, created_at FROM line_item WHERE item_id = <bread> ORDER BY created_at

GaveEntry
  id, expense_entry_id, recipient_name, amount

BorrowEntry
  id, user_id, counterparty_name, amount, direction (borrowed|repaid), created_at, note

Income
  id, user_id, amount, source, created_at
```

**Why this matters:** in v1, "bread" only existed as free text inside a notes field — you could
never query it. Promoting items to their own table is the single structural change that makes
the "how much has bread cost me" feature possible at all.

## 6. Core conversation flows

All amounts are formatted in Turkish Lira (₺) by default — this is a per-user setting
(`currency` on the User row), so it's a config value, not hardcoded, in case you support other
currencies later.

**Logging an expense (free text, no command needed):**
```
User: bread 100, milk 88, moucha 50
Bot:  Logged under FOOD — ₺238 total
      - bread ₺100  - milk ₺88  - moucha ₺50
      Today so far: ₺238
```

**Logging a GAVE entry:**
```
User: gave pedro 500 for data
Bot:  Logged ₺500 to Pedro under GAVE
      You've given Pedro ₺1,850 total this year.
```

**Item price history (the differentiator):**
```
User: how much has bread cost me the last 6 months?
Bot:  Bread has gone from ₺80 to ₺130 over the last 6 months, up 63%.
      [inline chart: Jan-Jun trend line]
```

```
User: what's gone up the most this year?
Bot:  Bread: up 63% (₺80 -> ₺130)
      Transport: up 40% (₺250 -> ₺350)
      Milk: up 18% (₺75 -> ₺88)
```

**Borrow tracking:**
```
User: /borrow 2000 from mum
Bot:  Noted — you owe Mum ₺2,000
      Use /repaid 500 mum when you pay some back.
```

**Ambiguous input (graceful fallback):**
```
User: 500 for stuff
Bot:  Got ₺500 — what category? [FOOD] [TRANSPORT] [OTHERS]
```

**On-demand summary (any time, not just scheduled)**

Summaries aren't limited to the weekly digest — the user can ask at any moment and get an
immediate answer, in natural language or via command:
```
User: how much have I spent this week?
Bot:  ₺4,120 this week so far.
      FOOD ₺2,900 (70%), TRANSPORT ₺820, OTHERS ₺400.

User: /summary month
Bot:  ₺17,340 this month.
      Biggest category: FOOD (₺11,200). Biggest single item: bread (₺1,040 across 12 entries).
```
`/summary today|week|month|year` covers the fixed windows; free-text asks like "how much did I
spend on transport in May" should also resolve, since the Claude parsing step handles the intent,
not just expense logging.

**Default window:** a bare `/summary` (or a vague "how much have I spent?") defaults to **today**,
not lifetime-to-date, and never forces the user to pick a window first. This matches the daily-habit
purpose of the bot and keeps the most common action frictionless — the reply should always echo the
window it used ("Today: ₺238") so the default is never ambiguous.

**Weekly digest (bot-initiated, opt-in — this is separate from on-demand summaries above)**
```
Bot: This week: ₺4,120 total. FOOD ₺2,900 (70%) — up ₺400 from last week.
     Bread alone was ₺390 of that — its price is creeping up, see /trends
```

## 7. HCI principles applied (checklist to hold the build to)

| Principle | How it shows up here |
|---|---|
| **Visibility of system status** | Every log gets an immediate confirmation with the running daily total — never a silent success |
| **Match between system and real world** | Categories and language mirror how the user already talks; "bread" stays "bread", not "SKU-04821" |
| **User control & freedom** | `/undo` reverses the last entry; `/edit` on any recent entry; nothing is a dead end |
| **Consistency & standards** | Same confirmation format for every entry type; commands and free text both work everywhere |
| **Error prevention** | Ambiguous category triggers quick-reply buttons instead of guessing wrong and logging silently |
| **Recognition over recall** | Weekly digest and price-trend alerts surface patterns proactively — user doesn't have to remember to ask |
| **Flexibility & efficiency** | Power users use slash commands (`/borrow`, `/trends`); casual users just type naturally |
| **Aesthetic & minimalist design** | One-line confirmations, no walls of text |
| **Help users recognize/recover from errors** | If parsing fails, bot echoes what it understood and asks for a fix, never fails silently |
| **Accessibility (adults, non-technical)** | No jargon, large-tap-target buttons for category correction, plain conversational input is the primary path — commands are optional, never required |

## 8. Tech stack (fast to build, cheap to run)

- **Bot framework:** `python-telegram-bot` or `grammY` (Node) — pick whichever you're faster in
- **Parsing:** Claude API (Haiku — this is a small extraction task, not reasoning-heavy) with a
  structured JSON output prompt: extract `{amount, category, items:[{name, amount}]}` from free text.
  Item name normalization ("loaf of bread" -> "bread") happens in this same prompt for MVP.
- **Database:** Supabase (Postgres, free tier, gives you a REST layer for a future dashboard for free)
- **Currency formatting:** ₺ (Turkish Lira) as the default display currency, formatted via
  `Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY' })` (or the Python equivalent) —
  keep it as a formatter, not hardcoded strings, since `currency` lives on the User row
- **Charts for `/trends`:** render server-side (e.g. QuickChart.io URL or a simple matplotlib PNG)
  and send as a Telegram image — no need for a web frontend to show a chart in the MVP
- **Hosting:** Railway or Fly.io
- **Scheduling (daily/weekly digests):** cron on the host or Supabase scheduled functions

## 9. Two-week build plan

**Days 1–2 — Foundation**
- Telegram bot registered, webhook/polling working
- Postgres schema from Section 5 migrated on Supabase (note the Item/LineItem split — get this
  right early, it's the hardest thing to retrofit later)
- `/start` creates a user row, asks currency + timezone

**Days 3–5 — Core logging**
- Claude-powered parser: free text -> structured JSON with item-level breakdown
- Multi-item splitting and item normalization
- Confirmation message format, `/undo`, `/edit`
- Ambiguous-category fallback with quick-reply buttons

**Days 6–7 — GAVE & Borrow**
- Person-tagged GAVE entries + per-person totals command
- Borrow/repaid ledger + balance command

**Days 8–9 — Price history & summaries**
- `/trends <item>` command — query LineItem history for one item, render a chart
- "what's gone up the most" query — compare first vs last N entries per item
- `/summary today|week|month|year` — on-demand summary, callable any time, not just scheduled
- Free-text summary intent handling ("how much did I spend on X in May") in the same Claude
  parsing step used for logging — the parser needs to distinguish a log-this-expense message
  from a tell-me-a-summary message
- Weekly digest job (opt-in, separate from on-demand `/summary`)

**Days 10–11 — Polish pass against Section 7's HCI checklist**
- Copy pass on every bot message
- Error states tested: garbled input, zero amount, missing category
- Multi-user isolation tested

**Days 12–13 — Deploy & real usage**
- Deploy to Railway/Fly.io
- Use it daily with real data, fix what breaks under real input

**Day 14 — Buffer**

## 10. Success signal for the MVP

Not "does it have features" — **do you personally keep logging past day 10 without being
reminded, and does asking "how much has X gone up" actually feel useful the first time you try
it.** That second part is the real test of whether the item-price feature is worth the extra
schema complexity.

## 11. Phase 2 — Wallet Trade Tracker

**Goal:** Let Web3-native users paste a crypto wallet address and have the bot monitor their on-chain trades, report daily P&L, and answer win-rate / loss questions on demand — without leaving Telegram.

### 11.1 What it does

| Feature | Description |
|---|---|
| Wallet linking | User pastes address + selects chain (ETH / BSC / SOL). Bot labels it optionally ("my main wallet"). Multiple wallets supported. |
| End-of-day P&L report | Every day at a configurable time, bot sends: total trades, realised P&L in USD, whether the day closed in profit or loss |
| On-demand win rate | `/winrate` or "what's my win rate?" → number of profitable trades ÷ total trades, all-time or filtered by date range |
| Loss/profit totals | "how much have I lost this month?" → sum of negative P&L trades for the period |
| Trade history | `/trades [today|week|month]` → paginated list of recent trades with token pair, entry/exit, P&L per trade |
| Wallet summary | `/wallet` → snapshot: total trades, win rate, total realised P&L, biggest win, biggest loss |

### 11.2 Data model additions

```
TrackedWallet
  id, user_id, address, chain (ETH|BSC|SOL|...), label (nullable), added_at, last_synced_at

Trade
  id, wallet_id, tx_hash (unique), token_in, token_out,
  amount_in, amount_out, price_usd, pnl_usd (nullable — null = open/unresolved),
  is_win (bool, nullable), traded_at, synced_at
```

`pnl_usd` is computed at sync time: for each swap, compare the USD value of what went in vs what came out. A trade is a "win" if `pnl_usd > 0`.

### 11.3 Conversation flows

**Linking a wallet:**
```
User: /wallet add
Bot:  Paste your wallet address:
User: 0xABC...
Bot:  Which chain? [ETH] [BSC] [SOL] [Other]
User: [SOL]
Bot:  Got it — tracking 0xABC... on Solana.
      Add a label? (e.g. "main wallet") — or skip.
User: main wallet
Bot:  Done. I'll send you a daily P&L at 9 PM. Use /wallet to see your summary any time.
```

**End-of-day report (bot-initiated):**
```
Bot: Daily report — main wallet (0xABC...)
     Today: 4 trades
     ✅ 2 wins  ❌ 2 losses
     Realised P&L: –$12.40
     You closed the day in a loss.
     Use /trades today for the full breakdown.
```

**On-demand win rate:**
```
User: what's my win rate?
Bot:  main wallet (0xABC...) — all time
      67 trades total: 44 wins, 23 losses
      Win rate: 65.7%
      Total realised P&L: +$340.20
```

**Loss query:**
```
User: how much have I lost this month?
Bot:  July losses: $89.50 across 8 losing trades.
      Biggest loss: $31.20 (BONK/SOL, Jul 14).
```

### 11.4 Tech additions needed

- **On-chain data:** [Moralis](https://moralis.io) or [Helius](https://helius.dev) (Solana) / [Alchemy](https://alchemy.com) (ETH/BSC) — all have free tiers for small usage. Pull swap transactions from their "wallet activity" endpoints.
- **Sync job:** background polling every 30 min (or webhook if the API supports it) to write new trades into the `Trade` table. `tx_hash` uniqueness prevents duplicates.
- **P&L calculation:** use USD value at time of swap (from the API's price data, not a live price — avoids race conditions).
- **Daily report job:** cron at configurable time per user, similar to the existing weekly digest job.

### 11.5 Scope boundaries

- **Read-only** — the bot never holds a private key and never submits transactions.
- MVP of this feature: one wallet per user, Solana only (most active trading chain for the target user). Multi-wallet + multi-chain is Phase 2b.
- Open positions (unrealised P&L) deferred — only closed/swap trades in scope.
- No tax reporting in scope.

---

## 12. Phase 2 — Savings Tracker

**Goal:** Help users build a consistent daily savings habit by logging deposits against a personal target, and surfacing streaks, missed days, and totals on demand.

### 12.1 What it does

| Feature | Description |
|---|---|
| First-time setup | On first `/saving` use, bot asks for the daily savings target. Stored once, editable later. |
| Daily logging | `saved 500` or `/saved 500` → logs ₺500 for today. Can log multiple times in a day (they sum). |
| Daily streak | Bot tracks consecutive days saved. Shown on every log confirmation. |
| Missed days report | On demand: how many days since goal was set where nothing was logged |
| Savings summary | `/savings` → total saved, days saved, days missed, current streak, target vs actual pace |
| Flexible queries | "how much have I saved this month?", "how many days did I miss this week?" |

### 12.2 Data model additions

```
SavingsGoal
  id, user_id, daily_target, currency, started_at, updated_at
  One row per user — upserted when user changes their target.

SavingsLog
  id, user_id, amount, date (DATE — one or more logs per day summed at query time), created_at
```

"Missed day" = a calendar day between `SavingsGoal.started_at` and today where `SUM(SavingsLog.amount)` for that date is 0.

### 12.3 Conversation flows

**First-time setup:**
```
User: /saving
Bot:  Let's set up your savings tracker.
      How much do you want to save each day? (in ₺)
User: 500
Bot:  Done — your daily savings target is ₺500.
      Log a saving any time with "saved [amount]" or /saved.
```

**Daily log:**
```
User: saved 500
Bot:  ₺500 saved today. 🔥 Day 4 in a row.
      This month: ₺6,500 saved across 13 days.
      Target pace: ₺10,500 — you're ₺4,000 behind.
```

**Partial save (below target):**
```
User: saved 200
Bot:  ₺200 saved today (₺300 short of your ₺500 target).
      You can log more later today — they'll stack.
```

**Summary on demand:**
```
User: /savings
Bot:  Savings summary
      Target: ₺500/day (since Jun 1)
      Days saved: 18 / 26
      Days missed: 8
      Total saved: ₺9,400
      Current streak: 4 days
      Best streak: 7 days (Jun 10–16)
```

**Missed days query:**
```
User: how many days did I miss this week?
Bot:  This week (Mon–today): 2 missed days (Tuesday, Thursday).
      You've saved ₺1,500 across 3 days.
```

**Changing target:**
```
User: /saving set 1000
Bot:  Daily target updated to ₺1,000.
      Miss days from before are still counted against your old ₺500 target.
```

### 12.4 Design decisions

- **No streaks broken by partial days** — if user logs any amount on a day, that day counts as "saved" regardless of whether it hit the target. Keeps the habit-forming loop rewarding. Missing means $0 logged.
- **Same-day stacking** — multiple logs on one day are summed, not overwritten. `saved 200` then `saved 300` = ₺500 for the day.
- **No retroactive logging** — can't log a save for yesterday. Keeps the data honest (same rule as expense logging).
- **Currency follows user setting** — uses the same `currency` from the User row, same `fmt()` formatter.

### 12.5 Tech additions needed

- Savings routing added to the Claude parser intent list (alongside expense/borrow/summary/trends intents) so "saved 500" routes correctly without needing `/saved`.
- Missed-day computation is a SQL gaps query: generate a series of dates from `started_at` to today, left-join to `SavingsLog`, filter where join is null.

---

## 13. General Phase 2 backlog (lower priority)

- WhatsApp channel via Business API
- Bank/card linking via Mono/Okra/Plaid
- Receipt photo OCR
- Fuzzy item-name matching (so "bread" and "loaf of bread" merge automatically)
- Full web dashboard
- Multi-currency support
- Auto-promoting repeated "OTHERS" items into real categories
- Timezone-aware daily totals (currently UTC)
