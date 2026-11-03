"""Place ONE small gold (XAU/USD) trade on Deriv with a hard $1 stake cap.

Run on your own computer (Deriv's API needs WebSocket, which cloud sessions block):

    pip install websockets
    python deriv_gold_trade.py --direction up      # or: --direction down

It asks for your Deriv API token (hidden input; create it with only Read + Trade
scopes), shows the trade, and sends it only after you type YES. Then it watches
the trade and closes it after --minutes if take-profit / stop-loss hasn't already.

Uses Deriv "Multipliers": your stake is the most you can lose on the trade.
"""
import argparse
import asyncio
import getpass
import json
import os
import sys
import time

import websockets

MAX_STAKE = 1.00  # hard cap in account currency; the script refuses anything above it
SYMBOL = "frxXAUUSD"
URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--direction", choices=["up", "down"], required=True, help="up = profit if gold rises")
    p.add_argument("--stake", type=float, default=1.00)
    p.add_argument("--take-profit", type=float, default=0.50, help="close when profit reaches this amount")
    p.add_argument("--stop-loss", type=float, default=0.50, help="close when loss reaches this amount")
    p.add_argument("--minutes", type=int, default=60, help="close the trade after this many minutes")
    a = p.parse_args()

    if a.stake > MAX_STAKE or a.stake <= 0:
        sys.exit(f"Refusing: stake must be between 0 and {MAX_STAKE}.")
    if a.stop_loss > a.stake:
        sys.exit("Refusing: stop loss can't be larger than the stake.")

    token = os.environ.get("DERIV_API_TOKEN") or getpass.getpass("Deriv API token (hidden): ")

    async with websockets.connect(URL) as ws:
        async def call(req):
            await ws.send(json.dumps(req))
            while True:
                r = json.loads(await ws.recv())
                if r.get("msg_type") == next(iter(req)) or "error" in r:
                    return r

        auth = await call({"authorize": token})
        if "error" in auth:
            sys.exit("Login failed: " + auth["error"]["message"])
        acc = auth["authorize"]
        cur = acc["currency"]
        kind = "DEMO (virtual money)" if acc["is_virtual"] else "REAL MONEY"
        print(f"Account {acc['loginid']} | {kind} | balance {acc['balance']} {cur}")

        # Find the multipliers Deriv offers for gold right now.
        cf = await call({"contracts_for": SYMBOL, "currency": cur})
        if "error" in cf:
            sys.exit("Gold not available: " + cf["error"]["message"])
        ctype = "MULTUP" if a.direction == "up" else "MULTDOWN"
        offers = [c for c in cf["contracts_for"]["available"] if c["contract_type"] == ctype]
        if not offers or not offers[0].get("multiplier_range"):
            sys.exit(f"No {ctype} multiplier contracts on gold right now (market may be closed).")
        mult = min(offers[0]["multiplier_range"])  # lowest multiplier = lowest risk
        min_stake = float(offers[0].get("min_stake", 0) or 0)
        if min_stake > a.stake:
            sys.exit(f"Deriv's minimum stake here is {min_stake} {cur}, above your {a.stake} cap. Not trading.")

        prop = await call({
            "proposal": 1, "amount": a.stake, "basis": "stake", "contract_type": ctype,
            "currency": cur, "symbol": SYMBOL, "multiplier": mult,
            "limit_order": {"take_profit": a.take_profit, "stop_loss": a.stop_loss},
        })
        if "error" in prop:
            sys.exit("Deriv rejected the trade: " + prop["error"]["message"])
        pr = prop["proposal"]
        print(f"\nTrade: gold {a.direction.upper()} x{mult} | stake {a.stake} {cur} | "
              f"take profit +{a.take_profit} | stop loss -{a.stop_loss} | closes after {a.minutes} min")
        print("Price now:", pr.get("spot"), "| Deriv says:", pr.get("longcode", "")[:200])
        if input(f"\nThis uses {kind}. Type YES to send: ").strip() != "YES":
            sys.exit("Not sent.")

        buy = await call({"buy": pr["id"], "price": a.stake})
        if "error" in buy:
            sys.exit("Buy failed: " + buy["error"]["message"])
        cid = buy["buy"]["contract_id"]
        print(f"Opened contract {cid}. Watching...")

        deadline = time.time() + a.minutes * 60
        while True:
            st = (await call({"proposal_open_contract": 1, "contract_id": cid})).get("proposal_open_contract", {})
            if st.get("is_sold"):
                print(f"Closed by take-profit/stop-loss. Profit: {st.get('profit')} {cur}")
                break
            print(f"  price {st.get('current_spot')} | profit now {st.get('profit')} {cur}")
            if time.time() >= deadline:
                sell = await call({"sell": cid, "price": 0})
                print("Time up, closed." if "error" not in sell else "Close failed: " + sell["error"]["message"],
                      "Sold for:", sell.get("sell", {}).get("sold_for"))
                break
            await asyncio.sleep(30)

        bal = await call({"balance": 1})
        print("Balance now:", bal.get("balance", {}).get("balance"), cur)


if __name__ == "__main__":
    asyncio.run(main())
