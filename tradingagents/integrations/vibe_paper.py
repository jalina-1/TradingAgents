"""Paper-trade TradingAgents decisions on a Vibe-Trading simulated account.

Vibe-Trading (https://github.com/HKUDS/Vibe-Trading) ships a daily paper
trading ledger: ``vibe-trading paper create/show/advance``. It settles orders
against completed daily bars, needs no broker account and no API key, and keeps
its accounts under ``~/.vibe-trading``.

This module is the bridge. It reads the paper account, hands it to the agents as
the portfolio context, turns the final 5-tier rating into an order sized against
the account, and settles that order with ``vibe-trading paper advance``.

Vibe-Trading is driven through its CLI in a subprocess rather than imported, so
it can live in its own virtualenv (its dependency pins differ from ours). Point
``VIBE_TRADING_BIN`` (or ``--vibe-bin``) at that venv's ``vibe-trading``.

Usage::

    python -m tradingagents.integrations.vibe_paper create --cash 100000
    python -m tradingagents.integrations.vibe_paper trade --account <id> \\
        --ticker NVDA --date 2026-11-03
    python -m tradingagents.integrations.vibe_paper status --account <id>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tradingagents.agents.rating import RATING_REVIEW, RATINGS_5_TIER

MARKETS = ("us", "hk", "cn_a")

# Fraction of the target position each rating aims for. ``None`` leaves the
# position alone. Bullish ratings never sell and bearish ratings never buy.
RATING_TARGETS: dict[str, float | None] = {
    "Buy": 1.0,
    "Overweight": 0.5,
    "Hold": None,
    "Underweight": None,  # handled as "trim half", see plan_order
    "Sell": 0.0,
}


class VibePaperError(RuntimeError):
    """The vibe-trading CLI failed or returned an error payload."""


# --------------------------------------------------------------------------- CLI


def vibe_bin(override: str | None = None) -> str:
    return override or os.environ.get("VIBE_TRADING_BIN") or "vibe-trading"


def run_vibe(args: list[str], bin_path: str | None = None) -> Any:
    """Run ``vibe-trading <args>`` and return its parsed JSON output."""
    cmd = [vibe_bin(bin_path), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise VibePaperError(
            f"{cmd[0]!r} not found. Install Vibe-Trading (pip install vibe-trading-ai) "
            "and set VIBE_TRADING_BIN to its vibe-trading executable."
        ) from exc
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    if proc.returncode != 0 or payload is None or (
        isinstance(payload, dict) and payload.get("status") == "error"
    ):
        detail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        raise VibePaperError(f"{' '.join(cmd)} failed (exit {proc.returncode}): {detail}")
    return payload


def create_account(
    cash: float, label: str, market: str = "us", start: str = "today", bin_path: str | None = None
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state.json"
        state.write_text(json.dumps({"cash": cash, "positions": []}), encoding="utf-8")
        return run_vibe(
            ["paper", "create", "--label", label, "--market", market,
             "--state", str(state), "--start", start],
            bin_path,
        )


def show_account(account_id: str, bin_path: str | None = None) -> dict:
    return run_vibe(["paper", "show", account_id], bin_path)


def advance_account(
    account_id: str, revision: int, to_date: str, orders: list[dict], bin_path: str | None = None
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "orders.json"
        path.write_text(json.dumps(orders), encoding="utf-8")
        return run_vibe(
            ["paper", "advance", account_id, "--revision", str(revision),
             "--to-date", to_date, "--orders", str(path)],
            bin_path,
        )


# ------------------------------------------------------------------- planning


def vibe_symbol(ticker: str, market: str) -> str:
    """TradingAgents ticker -> Vibe-Trading symbol (``NVDA`` -> ``NVDA.US``)."""
    ticker = ticker.strip().upper()
    if market == "us" and not ticker.endswith(".US"):
        return f"{ticker}.US"
    return ticker


def agent_ticker(symbol: str) -> str:
    """Vibe-Trading symbol -> the spelling the agents price (``NVDA.US`` -> ``NVDA``)."""
    return symbol[:-3] if symbol.upper().endswith(".US") else symbol


def to_portfolio_context(account: dict):
    """The paper account as the agents' PortfolioContext."""
    from tradingagents.portfolio import PortfolioContext, Position

    state = account["account_state"]
    return PortfolioContext(
        cash=state["cash"],
        currency=account.get("currency"),
        positions=[
            Position(ticker=agent_ticker(p["symbol"]), quantity=p["qty"],
                     average_price=p.get("avg_cost"))
            for p in state.get("positions", [])
        ],
    )


def account_equity(state: dict, symbol: str, price: float) -> float:
    """Cash plus holdings: ``symbol`` at ``price``, others at average cost."""
    total = float(state["cash"])
    for p in state.get("positions", []):
        mark = price if p["symbol"] == symbol else p.get("avg_cost", 0.0)
        total += float(p["qty"]) * float(mark)
    return total


@dataclass(frozen=True)
class OrderPlan:
    rating: str
    symbol: str
    held: float
    target: float
    order: dict | None
    reason: str


def plan_order(
    rating: str, state: dict, symbol: str, price: float, max_position_pct: float
) -> OrderPlan:
    """Turn a 5-tier rating into at most one whole-share market order.

    Buy aims for ``max_position_pct`` of equity in the name, Overweight for half
    of that; neither sells. Underweight trims the holding by half, Sell closes
    it, Hold and REVIEW place nothing. Buys are capped by free cash.
    """
    held = sum(float(p["qty"]) for p in state.get("positions", []) if p["symbol"] == symbol)

    def plan(target: float, reason: str) -> OrderPlan:
        delta = target - held
        order = None
        if delta >= 1:
            order = {"symbol": symbol, "side": "buy", "qty": float(math.floor(delta))}
        elif delta <= -1 or (target == 0 and held > 0):
            order = {"symbol": symbol, "side": "sell", "qty": float(min(held, math.ceil(-delta)))}
        return OrderPlan(rating, symbol, held, target, order, reason)

    if rating == RATING_REVIEW or rating not in RATINGS_5_TIER:
        return OrderPlan(rating, symbol, held, held, None, "no tradeable rating; needs review")
    if rating == "Hold":
        return OrderPlan(rating, symbol, held, held, None, "hold: no change")
    if rating == "Sell":
        return plan(0.0, "sell: close the position")
    if rating == "Underweight":
        return plan(float(math.floor(held / 2)), "underweight: trim the position by half")

    if price <= 0:
        raise ValueError(f"price for {symbol} must be positive, got {price}")
    weight = RATING_TARGETS[rating] * max_position_pct / 100.0
    wanted = math.floor(account_equity(state, symbol, price) * weight / price)
    affordable = held + math.floor(float(state["cash"]) / price)
    target = float(max(held, min(wanted, affordable)))
    return plan(target, f"{rating.lower()}: target {weight:.1%} of equity at {price:,.2f}")


def last_close(ticker: str, on_or_before: str) -> float:
    """Last daily close at or before ``on_or_before`` (Yahoo, via the data layer)."""
    from tradingagents.dataflows.vendors.yahoo.market import get_closes

    end = date.fromisoformat(on_or_before) + timedelta(days=1)
    closes = get_closes(ticker, (end - timedelta(days=10)).isoformat(), end.isoformat()).dropna()
    closes = closes[closes > 0]
    if closes.empty:
        raise VibePaperError(f"no price for {ticker} on or before {on_or_before}")
    return float(closes.iloc[-1])


def previous_weekday(day: str) -> str:
    d = date.fromisoformat(day) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


# ------------------------------------------------------------------------ run


def run_agents(ticker: str, analysis_date: str, account: dict) -> str:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    _, signal = graph.propagate(ticker, analysis_date, portfolio=to_portfolio_context(account))
    return signal


def trade(args: argparse.Namespace) -> dict:
    account = show_account(args.account, args.vibe_bin)
    market = account["market"]
    symbol = args.symbol or vibe_symbol(args.ticker, market)
    analysis_date = args.analysis_date or previous_weekday(args.date)

    rating = args.rating or run_agents(args.ticker, analysis_date, account)
    price = args.price or last_close(args.ticker, analysis_date)
    plan = plan_order(rating, account["account_state"], symbol, price, args.max_position_pct)
    orders = [plan.order] if plan.order else []

    out: dict[str, Any] = {
        "account": args.account,
        "ticker": args.ticker,
        "symbol": symbol,
        "analysis_date": analysis_date,
        "execution_date": args.date,
        "rating": rating,
        "reference_price": price,
        "held": plan.held,
        "target": plan.target,
        "orders": orders,
        "reason": plan.reason,
    }
    if args.dry_run:
        out["dry_run"] = True
        return out
    result = advance_account(args.account, account["revision"], args.date, orders, args.vibe_bin)
    out["result"] = result.get("last_result", result)
    out["account_state"] = result.get("account_state")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tradingagents.integrations.vibe_paper")
    parser.add_argument("--vibe-bin", help="vibe-trading executable (default: $VIBE_TRADING_BIN)")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="create a paper account")
    c.add_argument("--cash", type=float, default=100_000.0)
    c.add_argument("--label", default="tradingagents")
    c.add_argument("--market", choices=MARKETS, default="us")
    c.add_argument("--start", choices=("today", "next_session"), default="today")

    s = sub.add_parser("status", help="show the account and its NAV history")
    s.add_argument("--account", required=True)

    t = sub.add_parser("trade", help="analyze a ticker and settle the resulting order")
    t.add_argument("--account", required=True)
    t.add_argument("--ticker", required=True)
    t.add_argument("--date", required=True,
                   help="execution session (YYYY-MM-DD); must be a completed trading day")
    t.add_argument("--analysis-date",
                   help="date the agents analyze (default: the weekday before --date)")
    t.add_argument("--symbol", help="Vibe-Trading symbol, if not <TICKER>.US")
    t.add_argument("--rating", choices=(*RATINGS_5_TIER, RATING_REVIEW),
                   help="skip the agents and use this rating")
    t.add_argument("--price", type=float, help="reference price for sizing (default: last Yahoo close)")
    t.add_argument("--max-position-pct", type=float, default=10.0,
                   help="equity %% a Buy targets in one name (default 10)")
    t.add_argument("--dry-run", action="store_true", help="plan the order without settling it")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            out = create_account(args.cash, args.label, args.market, args.start, args.vibe_bin)
        elif args.command == "status":
            out = {
                "account": show_account(args.account, args.vibe_bin),
                "history": run_vibe(["paper", "history", args.account], args.vibe_bin),
            }
        else:
            out = trade(args)
    except (VibePaperError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
