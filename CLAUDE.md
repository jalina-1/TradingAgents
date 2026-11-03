# TradingAgents: how to run it and the prompts

> **Cloud sessions:** here `~/TradingAgents` means this repository checkout (e.g. `/home/user/TradingAgents`). Each new cloud session starts fresh, so first run `uv venv --python 3.13 && uv pip install .`, recreate `.env` (Part 1 Step 5 of the setup guide; it is git-ignored), and sign in again with `.venv/bin/tradingagents login chatgpt`.

## PART 2: HOW TO RUN AN ANALYSIS (reference for Claude)

Whenever a prompt asks you to run TradingAgents, follow these rules.

**The command.** Always start with `cd ~/TradingAgents &&` so the `.env` settings load. Without them the run stops with "No terminal to answer the setup questions".
```bash
cd ~/TradingAgents && .venv/bin/tradingagents --ticker TICKER --date YYYY-MM-DD --analysts LIST --save --no-show < /dev/null
```
- **Date:** today, unless the user names one. Data is used as of that day's close.
- **Analysts:**
  - Stocks: `market,sentiment,news,fundamentals`.
  - Crypto (`BTC-USD`, `ETH-USD`, `SOL-USD`) and funds/ETFs (`SPY`, `QQQ`, `GLD`, `TLT`, sector ETFs): `market,sentiment,news`. They have no company fundamentals.
  - "Quick mode" means `--analysts market` only. Cheaper, good for screening a list.
- **The user's holdings:** add `--portfolio my_portfolio.json`. The trader, risk team and portfolio manager then judge the position the user already holds. See the file format below.
- **Timing:** a run takes about 2–8 minutes depending on the model and how many analysts run (ChatGPT plans tend to be slower). Always run it in the background and wait for "Analysis Complete". Run at most **2 tickers at the same time**; more triggers rate limits on Reddit data.
- **Finding the report:** the printed path wraps across lines, so don't parse it. Take the newest folder for that ticker instead (folders are named by when the run happened, not the analysis date):
  ```bash
  REPORT=$(ls -td ~/.tradingagents/logs/reports/TICKER_* | head -1)
  ```
  Inside it:
  - `1_analysts/` (market, sentiment, news, fundamentals reports)
  - `2_research/` (`bull.md`, `bear.md`, `manager.md`)
  - `3_trading/trader.md` (entry, stop-loss, sizing)
  - `4_risk/` (aggressive, conservative, neutral)
  - `5_portfolio/decision.md` (final rating, summary, price target, time horizon)
  - `complete_report.html`: the whole report as one page. Open it for the user when a prompt asks for a report.
- **Ratings,** from most to least bullish: **Buy, Overweight, Hold, Underweight, Sell.** Overweight means "own more than a normal-sized position", a milder Buy. Underweight means "own less", a milder Sell. Explain this the first time a rating comes up.
- **Backtests** (scoring past calls):
  ```bash
  cd ~/TradingAgents && .venv/bin/tradingagents backtest TICKER --start YYYY-MM-DD --end YYYY-MM-DD --every 7 --analysts market < /dev/null
  ```
  - It runs one analysis per date, so each date costs about as much as one quick run.
  - For crypto tickers (`BTC-USD`, `ETH-USD` and so on) add `--asset-type crypto`. A normal analysis detects crypto by itself; a backtest assumes stocks unless told.
  - Each call is scored over the 5 trading days after the date, so the end date must be at least 8 trading days ago.
  - It prints a summary per rating (number of calls and average "alpha", the return against the market) and the path of a log with each date's call and outcome. Read that log to build a date-by-date table.
- **Memory:** the first run for each ticker and date is saved in `~/.tradingagents/memory/trading_memory.md`. Later runs score those calls once their holding period has passed and learn from them. A repeat run of the same ticker and date isn't saved again.
- **When you summarise a run:** plain English, short. Lead with the rating. Say when an analyst reported missing data (for example "news unavailable"), so the user knows what the call is based on.
- **Never place real trades.** This is research, not financial advice.

**Portfolio file** (`~/TradingAgents/my_portfolio.json`). Help the user fill it in when a prompt needs it:
```json
{
  "cash": 5000,
  "currency": "USD",
  "positions": [
    {"ticker": "NVDA", "quantity": 40, "average_price": 197.58},
    {"ticker": "BTC-USD", "quantity": 0.15, "average_price": 60000}
  ]
}
```

**Investor profile** (`~/TradingAgents/investor_profile.md`). A short plain-English document about the user: goals, age, time horizon, how much risk they're comfortable with, their investing style, and any fixed rules (for example "keep 10% cash at all times", "hold 20% gold", "at least 10% Bitcoin"). TradingAgents doesn't read it; you do. When a prompt mentions the investor profile, help the user write it if it doesn't exist yet, then weigh the desk's ratings against it and point out any rule a suggested change would break.

**Troubleshooting**

| Message | What it means |
|---|---|
| `No terminal to answer the setup questions` | A setting is missing from `.env` (Step 5), a flag is missing, or the command didn't start with `cd ~/TradingAgents &&`. The message lists what's missing. |
| `not scoped to a workspace` | The Claude key needs to be created inside a workspace in the Anthropic console. |
| `invalid x-api-key` | The key in `.env` is wrong or revoked. Paste a new one. |
| `Not signed in to ChatGPT` | Run the login again (Step 4A). |
| `Model 'claude-sonnet-5-5' is not in the known model list ... Continuing anyway` | Harmless. The model is newer than TradingAgents' built-in list. |
| `structured-output invocation failed ... retrying once as free text` | Harmless. The run recovers by itself. |
| `FRED_API_KEY ... not set` | Harmless. The macro section is skipped. Add a free FRED key to fix it. |
| `news unavailable` / `HTTP Error 429` | The free news or Reddit source was empty or rate-limited. The run continues without it. |

## PART 3: THE PROMPTS

Paste any of these into Claude Code. Replace anything in [BRACKETS].

### 1. Second opinion: before you buy anything
```
Run TradingAgents on [TICKER] for today with the full team. I'm thinking about buying $[AMOUNT] and I currently hold [NOTHING / X shares at $Y]. Tell me in plain English: the final rating, the bull's best argument, the bear's best argument, the price that would prove the trade wrong, and whether the desk would make my buy smaller. Then open the full report.
```

### 2. The alpha hunt: rank my watchlist
```
Run TradingAgents in quick mode on each of these: [NVDA, AMD, AVGO, TSM, PLTR, META]. Rank them from most to least bullish in a table with one line on why. Then run the full team on the top 2 and tell me which one has the stronger case and what would make it wrong.
```

### 3. Full portfolio review
```
Help me fill in my_portfolio.json with my holdings and write my investor_profile.md if I don't have one. Then run TradingAgents on every holding, with the portfolio file, so each call takes my whole portfolio into account. Give me a table with each holding's rating and a one-line reason, then rank the 3 biggest changes the desk suggests in line with my investor profile, starting with the most urgent. Flag any change that would break one of my rules.
```

### 4. Concentration check: where am I overexposed?
```
Look at my_portfolio.json and my investor_profile.md. Group my holdings by sector, asset type and theme (for example AI, crypto, gold, big tech) and show me what percentage of my portfolio each one is. Then run TradingAgents in quick mode on my 3 largest exposures and tell me where I'm more concentrated than my profile says I should be, and which position the desk would trim first.
```

### 5. Devil's advocate on my biggest position
```
My biggest position is [TICKER], about [X]% of my portfolio. Run TradingAgents on it with my portfolio file. Give me only the bear's case and the risk team's view on my position size. Don't soften it. Finish with the one price level or event that would make the bear right.
```

### 6. Earnings prep: the night before a report
```
[TICKER] reports earnings [DATE]. Run TradingAgents on it for today. Tell me what the bull and bear each expect, the price levels the desk says matter after the report, and whether it would hold through earnings or trim first.
```

### 7. Sector scan: the best 3 in a sector
```
I'm bullish on [SEMICONDUCTOR] stocks. Make a list of the 50 largest US-listed [SEMICONDUCTOR] companies by market cap. Run TradingAgents in quick mode on each one, at most 2 at a time, and rank them from most to least bullish in a table. Then run the full team on the top 3 and tell me which one has the strongest case and what would make each one wrong. (This scans a lot of stocks, so it costs more than a single run.)
```

### 8. Sector rotation: where's the strength?
```
Run TradingAgents in quick mode on these sector ETFs: XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU. Rank them from most to least bullish with one line each. Then run the full team (market, sentiment, news) on the top and bottom one and explain the difference.
```

### 9. The honesty test: does it actually work?
```
Test whether TradingAgents' calls on [TICKER] would have made money. Use its backtest command in quick mode for every 7 days over [THE LAST 2 MONTHS], ending at least 2 weeks ago. Show me a simple table: date, rating, what the price did afterwards, and whether the call beat the market. Tell me honestly whether it looks better than just holding.
```

### 10. Claude vs ChatGPT: same stock, two brains
*(needs both set up: a Claude key in .env and a ChatGPT sign-in)*
```
Run TradingAgents on [TICKER] for the same date twice: once on Claude, once on my ChatGPT plan (for the ChatGPT run, put TRADINGAGENTS_LLM_PROVIDER=chatgpt and both model settings in front of the command). Compare the two final calls side by side: where they agree, where they differ, and which arguments each one weighted most.
```

### 11. Morning brief: every day, automatically
```
Set up a daily job on this computer that runs at [7:00am] on weekdays. It should run TradingAgents on each holding in my_portfolio.json, with the portfolio file, and save a short summary file: each holding's rating, any rating that changed since yesterday, and the top 3 suggested changes. Use the scheduler built into my operating system, show me how to turn it off, and run it once now so I can see the summary. Tell me plainly that it only runs while this computer is on and awake at that time.
```

### 12. Paper trading: let the desk place practice trades (advanced)
*(needs a free Alpaca account switched to Paper Trading, and its paper API keys)*
```
Connect TradingAgents to my Alpaca PAPER trading account. Install alpaca-py into the TradingAgents environment, add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env, open .env so I can paste my keys, and never ask for them in chat. Refuse to run if the key doesn't start with "PK" or the account isn't a paper account. Then write a small script that reads the latest rating for each holding and shows me the paper orders it would place (Buy/Overweight adds, Underweight/Sell trims). Show me a dry run first and only place the paper orders after I type YES.
```

### 13. One paper order: act on a call
*(needs prompt 12 set up first)*
```
TradingAgents just rated [TICKER] a [RATING]. Place a PAPER order on my Alpaca account to buy $[1,000] of [TICKER] at the current market price. Check the account is a paper account first, show me the order before sending it, and only send it after I type YES. If the market is closed, tell me the order will fill at the next open.
```

---

*Built for the Miles Deutscher Finance video on TradingAgents (github.com/TauricResearch/TradingAgents). Research only. Not financial advice. Paper trading only.*
