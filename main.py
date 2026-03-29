import os
import logging
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import httpx
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN  = os.environ["BOT_TOKEN"]
API_SECRET = os.environ.get("API_SECRET", "changeme")

_raw_users = os.environ.get("NOTIFY_USERS", os.environ.get("ADMIN_CHAT_ID", ""))
NOTIFY_USERS = [u.strip() for u in _raw_users.split(",") if u.strip()]

orders = {}

async def tg_send(text):
    async with httpx.AsyncClient() as client:
        for user_id in NOTIFY_USERS:
            try:
                await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": user_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
            except Exception as e:
                logger.error(f"Failed to send to {user_id}: {e}")

async def check_btc(address, expected):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://blockstream.info/api/address/{address}", timeout=10)
            if r.status_code == 200:
                balance = r.json().get("chain_stats", {}).get("funded_txo_sum", 0) / 1e8
                return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_eth(address, expected, coin="ETH"):
    try:
        async with httpx.AsyncClient() as c:
            if coin == "ETH":
                r = await c.get(f"https://api.etherscan.io/api?module=account&action=balance&address={address}&tag=latest", timeout=10)
                if r.status_code == 200:
                    balance = int(r.json().get("result", 0)) / 1e18
                    return balance if balance >= expected * 0.95 else None
            else:
                r = await c.get(f"https://api.etherscan.io/api?module=account&action=tokenbalance&contractaddress=0xdac17f958d2ee523a2206206994597c13d831ec7&address={address}&tag=latest", timeout=10)
                if r.status_code == 200:
                    balance = int(r.json().get("result", 0)) / 1e6
                    return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_trx(address, expected, coin="TRX"):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://apilist.tronscanapi.com/api/accountv2?address={address}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                if coin == "TRX":
                    balance = data.get("balance", 0) / 1e6
                    return balance if balance >= expected * 0.95 else None
                usdt = next((t for t in data.get("trc20token_balances", []) if t.get("tokenAbbr") == "USDT"), None)
                if usdt:
                    balance = float(usdt.get("balance", 0)) / 1e6
                    return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_sol(address, expected):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post("https://api.mainnet-beta.solana.com", json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}, timeout=10)
            if r.status_code == 200:
                balance = r.json().get("result", {}).get("value", 0) / 1e9
                return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_blockcypher(address, expected, coin):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://api.blockcypher.com/v1/{coin}/main/addrs/{address}/balance", timeout=10)
            if r.status_code == 200:
                balance = r.json().get("balance", 0) / 1e8
                return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_xrp(address, expected):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post("https://xrplcluster.com/", json={"method": "account_info", "params": [{"account": address, "ledger_index": "validated"}]}, timeout=10)
            if r.status_code == 200:
                balance = float(r.json().get("result", {}).get("account_data", {}).get("Balance", 0)) / 1e6
                return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_ton(address, expected):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://tonapi.io/v2/accounts/{address}", timeout=10)
            if r.status_code == 200:
                balance = r.json().get("balance", 0) / 1e9
                return balance if balance >= expected * 0.95 else None
    except: pass
    return None

async def check_payment(coin, address, amount):
    c = coin.upper().replace(" ", "_")
    if c == "BTC": return await check_btc(address, amount)
    elif c in ("ETH", "ETH_ERC20", "BNB", "BNB_BEP20", "LINK", "LINK_ERC20", "DAI", "DAI_ERC20"): return await check_eth(address, amount, "ETH")
    elif c in ("USDT_ERC", "USDT_ERC20"): return await check_eth(address, amount, "USDT")
    elif c in ("USDT_TRC", "USDT_TRC20"): return await check_trx(address, amount, "USDT")
    elif c in ("TRX", "TRON_TRX"): return await check_trx(address, amount, "TRX")
    elif c in ("SOL", "SOLANA_SOL"): return await check_sol(address, amount)
    elif c == "LTC": return await check_blockcypher(address, amount, "ltc")
    elif c == "DOGE": return await check_blockcypher(address, amount, "doge")
    elif c == "XRP": return await check_xrp(address, amount)
    elif c == "TON": return await check_ton(address, amount)
    return None

async def payment_checker():
    while True:
        await asyncio.sleep(30)
        for code, order in {k: v for k, v in orders.items() if v["status"] == "pending"}.items():
            try:
                received = await check_payment(order["coin"], order["deposit_address"], float(order["amount"]))
                if received is not None:
                    orders[code]["status"] = "received"
                    await tg_send(f"✅ *Payment Received!*\n\n🪙 *Coin:* {order['coin']}\n💰 *Amount:* {received:.6f}\n🔑 *Session:* `{code}`\n📥 *Address:* `{order['deposit_address']}`")
            except Exception as e:
                logger.error(f"Checker error {code}: {e}")

@app.on_event("startup")
async def startup():
    asyncio.create_task(payment_checker())
    logger.info(f"Started. Users: {NOTIFY_USERS}")

class Addr(BaseModel):
    address: str
    pct: str

class MixPayload(BaseModel):
    secret: str
    coin: str
    amount: str
    delay: str
    session_code: str
    deposit_address: str
    addresses: List[Addr]

@app.post("/api/notify")
async def notify(data: MixPayload):
    if data.secret != API_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    orders[data.session_code] = {"coin": data.coin, "amount": data.amount, "deposit_address": data.deposit_address, "addresses": [{"address": a.address, "pct": a.pct} for a in data.addresses], "status": "pending", "created_at": datetime.utcnow().isoformat()}
    addr_text = "\n".join(f"  {i+1}. `{a.address}` - {a.pct}" for i, a in enumerate(data.addresses)) or "  -"
    await tg_send(f"🔀 *New Mix!*\n\n🪙 *Coin:* {data.coin}\n💰 *Amount:* {data.amount}\n⏱ *Delay:* {data.delay}h\n🔑 *Session:* `{data.session_code}`\n📥 *Deposit:* `{data.deposit_address}`\n\n📬 *Recipients:*\n{addr_text}\n\n⏳ Watching blockchain every 30s...")
    return {"ok": True}

@app.get("/")
def health():
    return {"status": "ok", "orders": len(orders), "users": len(NOTIFY_USERS)}
