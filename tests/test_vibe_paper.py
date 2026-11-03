"""The Vibe-Trading paper bridge: rating -> order sizing and CLI plumbing."""

import json
import subprocess

import pytest

from tradingagents.integrations import vibe_paper as vp

FLAT = {"cash": 100_000.0, "positions": []}


def held(qty, cash=50_000.0, avg=100.0):
    return {"cash": cash, "positions": [{"symbol": "NVDA.US", "qty": qty, "avg_cost": avg}]}


def test_symbol_mapping():
    assert vp.vibe_symbol("nvda", "us") == "NVDA.US"
    assert vp.vibe_symbol("NVDA.US", "us") == "NVDA.US"
    assert vp.vibe_symbol("0700.HK", "hk") == "0700.HK"


def test_buy_targets_max_position_of_equity():
    plan = vp.plan_order("Buy", FLAT, "NVDA.US", 200.0, 10)
    assert plan.order == {"symbol": "NVDA.US", "side": "buy", "qty": 50.0}


def test_overweight_targets_half_and_never_sells():
    assert vp.plan_order("Overweight", FLAT, "NVDA.US", 200.0, 10).order["qty"] == 25.0
    # Already above the Overweight target: no sale on a bullish rating.
    assert vp.plan_order("Overweight", held(400), "NVDA.US", 100.0, 10).order is None


def test_buy_capped_by_cash():
    plan = vp.plan_order("Buy", {"cash": 1_000.0, "positions": []}, "NVDA.US", 300.0, 100)
    assert plan.order["qty"] == 3.0


def test_sell_closes_and_underweight_trims():
    assert vp.plan_order("Sell", held(7), "NVDA.US", 100.0, 10).order == {
        "symbol": "NVDA.US", "side": "sell", "qty": 7.0}
    assert vp.plan_order("Underweight", held(7), "NVDA.US", 100.0, 10).order["qty"] == 4.0
    assert vp.plan_order("Sell", FLAT, "NVDA.US", 100.0, 10).order is None


@pytest.mark.parametrize("rating", ["Hold", "REVIEW", "garbage"])
def test_no_order_without_actionable_rating(rating):
    assert vp.plan_order(rating, held(5), "NVDA.US", 100.0, 10).order is None


def test_previous_weekday_skips_weekend():
    assert vp.previous_weekday("2026-11-02") == "2026-10-30"  # Monday -> Friday
    assert vp.previous_weekday("2026-11-03") == "2026-11-02"


def test_portfolio_context_from_account():
    ctx = vp.to_portfolio_context({"currency": "USD", "account_state": held(7)})
    assert ctx.cash == 50_000.0 and ctx.currency == "USD"
    assert ctx.position_in("NVDA").quantity == 7


def test_run_vibe_raises_on_error_payload(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": "error", "message": "x"}), "")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(vp.VibePaperError):
        vp.run_vibe(["paper", "list"])


def test_run_vibe_missing_binary(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(vp.VibePaperError, match="VIBE_TRADING_BIN"):
        vp.run_vibe(["paper", "list"], "/nope/vibe-trading")


def test_trade_with_fixed_rating_settles_order(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[2] == "show":
            out = {"market": "us", "revision": 3, "currency": "USD", "account_state": FLAT}
        else:
            orders = json.loads(open(cmd[cmd.index("--orders") + 1]).read())
            out = {"revision": 4, "account_state": FLAT, "last_result": {"orders": orders}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    rc = vp.main(["--vibe-bin", "vt", "trade", "--account", "a1", "--ticker", "NVDA",
                  "--date", "2026-11-03", "--rating", "Buy", "--price", "200"])
    assert rc == 0
    advance = calls[-1]
    assert advance[:4] == ["vt", "paper", "advance", "a1"]
    assert advance[advance.index("--revision") + 1] == "3"
    assert advance[advance.index("--to-date") + 1] == "2026-11-03"
