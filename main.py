import os
import time
import asyncio
import logging
from typing import List, Dict, Any
from fastapi import FastAPI
import uvicorn
import ccxt.async_support as ccxt
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SentinelBot")

# Environment & Config
PORT = int(os.getenv("PORT", "10000"))
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
API_KEY = os.getenv("BINANCE_API_KEY", "")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Active symbols to scan (high liquidity, high volume)
SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "DOGE/USDT:USDT",
    "XRP/USDT:USDT",
    "SUI/USDT:USDT",
    "AVAX/USDT:USDT",
    "BNB/USDT:USDT",
    "LINK/USDT:USDT",
    "NEAR/USDT:USDT"
]

LEVERAGE = 3  # Safe 3x leverage
MAX_RISK_USD = 0.35  # Max $0.35 risk per trade on micro account
TARGET_RR = 2.5  # 1 : 2.5 Risk-to-Reward ratio

app = FastAPI(title="Binance Sentinel Bot", version="2.0.0")

# State
bot_state = {
    "status": "RUNNING",
    "paper_trading": PAPER_TRADING,
    "scanned_symbols": len(SYMBOLS),
    "active_positions": {},
    "trade_history": [],
    "last_scan_time": None
}

def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info(f"[TG LOG] {msg}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

@app.get("/")
def index():
    return {
        "bot": "Binance Sentinel Multi-Pair Bot",
        "state": bot_state,
        "note": "24/7 Cloud Sentinel Active"
    }

@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": time.time()}

# Quant Indicators
def calculate_indicators(candles: List[List[float]]) -> Dict[str, Any]:
    # candles: [timestamp, open, high, low, close, volume]
    if len(candles) < 30:
        return {}
    
    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    volumes = [c[5] for c in candles]

    # EMA 200 / EMA 50
    def ema(values, period):
        k = 2 / (period + 1)
        res = [values[0]]
        for v in values[1:]:
            res.append(v * k + res[-1] * (1 - k))
        return res[-1]

    ema_fast = ema(closes, 14)
    ema_slow = ema(closes, 50)

    # RSI 14
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 1
    avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1
    rs = avg_gain / (avg_loss if avg_loss != 0 else 1)
    rsi = 100 - (100 / (1 + rs))

    # ATR 14
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(candles))]
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else (highs[-1] - lows[-1])

    # Volume Spike: Current vs average of last 20
    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else volumes[-1]
    vol_ratio = volumes[-1] / (avg_vol if avg_vol > 0 else 1)

    return {
        "close": closes[-1],
        "high": highs[-1],
        "low": lows[-1],
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": rsi,
        "atr": atr,
        "vol_ratio": vol_ratio
    }

async def trading_loop():
    logger.info(f"Starting Trading Engine | PAPER_TRADING = {PAPER_TRADING}")
    send_telegram(f"🤖 *Binance Sentinel Bot Başlatıldı!*\nMod: `{'PAPER TRADING (Demo)' if PAPER_TRADING else 'CANLI BINANCE'}`\nTakip Edilen: `{len(SYMBOLS)} Parite`\nKaldıraç: `{LEVERAGE}x` | Risk/İşlem: `${MAX_RISK_USD}`")

    exchange = ccxt.binance({
        "apiKey": API_KEY,
        "secret": SECRET_KEY,
        "enableRateLimit": True,
        "options": {"defaultType": "future"}
    })

    try:
        while True:
            bot_state["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            
            for symbol in SYMBOLS:
                try:
                    # Fetch 15m OHLCV
                    ohlcv = await exchange.fetch_ohlcv(symbol, timeframe="15m", limit=60)
                    ind = calculate_indicators(ohlcv)
                    if not ind:
                        continue

                    # Strategy Setup:
                    # 1. Trend Alignment: EMA fast > EMA slow (Uptrend) or EMA fast < EMA slow (Downtrend)
                    # 2. Volume Spike: Volume > 2.0x average
                    # 3. Pullback / Mean Reversion: RSI between 35-45 for Long pullback, 55-65 for Short pullback
                    signal = None
                    entry = ind["close"]
                    atr = ind["atr"]

                    if ind["ema_fast"] > ind["ema_slow"] and ind["vol_ratio"] >= 1.8 and ind["rsi"] < 45:
                        signal = "LONG"
                        stop_loss = entry - (atr * 1.5)
                        take_profit = entry + (atr * 1.5 * TARGET_RR)
                    elif ind["ema_fast"] < ind["ema_slow"] and ind["vol_ratio"] >= 1.8 and ind["rsi"] > 55:
                        signal = "SHORT"
                        stop_loss = entry + (atr * 1.5)
                        take_profit = entry - (atr * 1.5 * TARGET_RR)

                    # Check if already in position for this symbol
                    if signal and symbol not in bot_state["active_positions"]:
                        # Calculate position size based on max risk $0.35
                        risk_distance = abs(entry - stop_loss)
                        qty = MAX_RISK_USD / (risk_distance if risk_distance > 0 else 1)
                        
                        trade_info = {
                            "symbol": symbol,
                            "side": signal,
                            "entry": entry,
                            "stop_loss": round(stop_loss, 4),
                            "take_profit": round(take_profit, 4),
                            "qty": round(qty, 4),
                            "time": time.strftime("%H:%M:%S")
                        }
                        
                        bot_state["active_positions"][symbol] = trade_info
                        logger.info(f"🔥 SIGNAL TRIGGERED: {trade_info}")
                        
                        tg_msg = (
                            f"⚡ *YENİ İŞLEM TETİKLENDİ!*\n"
                            f"Parite: `{symbol}`\n"
                            f"Yön: `{'🟢 LONG' if signal == 'LONG' else '🔴 SHORT'}`\n"
                            f"Giriş: `${entry}`\n"
                            f"Zarar Durdur (SL): `${trade_info['stop_loss']}`\n"
                            f"Kâr Al (TP): `${trade_info['take_profit']}`\n"
                            f"Risk/Ödül: `1 : {TARGET_RR}`\n"
                            f"Mod: `{'Sanal Demo' if PAPER_TRADING else 'Canlı Emir'}`"
                        )
                        send_telegram(tg_msg)

                        # If LIVE TRADING: Send native Binance Futures market + STOP_MARKET orders
                        if not PAPER_TRADING and API_KEY and SECRET_KEY:
                            try:
                                # Set leverage
                                clean_sym = symbol.replace("/USDT:USDT", "USDT")
                                await exchange.set_leverage(LEVERAGE, symbol)
                                # Market entry
                                order = await exchange.create_market_order(symbol, "buy" if signal == "LONG" else "sell", qty)
                                # Borsa-side stop loss order
                                sl_order = await exchange.create_order(
                                    symbol=symbol,
                                    type="STOP_MARKET",
                                    side="sell" if signal == "LONG" else "buy",
                                    amount=qty,
                                    params={"stopPrice": trade_info["stop_loss"], "reduceOnly": True}
                                )
                                logger.info(f"Live orders placed for {symbol}: entry={order['id']}, sl={sl_order['id']}")
                            except Exception as ex:
                                logger.error(f"Live trade execution failed for {symbol}: {ex}")

                except Exception as ex:
                    logger.debug(f"Scan error on {symbol}: {ex}")

                await asyncio.sleep(1)  # rate limit buffer

            # Check open positions for exit in paper trading mode
            for sym, pos in list(bot_state["active_positions"].items()):
                try:
                    ticker = await exchange.fetch_ticker(sym)
                    curr = ticker["last"]
                    hit_tp = (pos["side"] == "LONG" and curr >= pos["take_profit"]) or (pos["side"] == "SHORT" and curr <= pos["take_profit"])
                    hit_sl = (pos["side"] == "LONG" and curr <= pos["stop_loss"]) or (pos["side"] == "SHORT" and curr >= pos["stop_loss"])

                    if hit_tp or hit_sl:
                        outcome = "KÂR ALINDI (TP) 🎯" if hit_tp else "STOP OLDU (SL) 🛑"
                        pnl = (MAX_RISK_USD * TARGET_RR) if hit_tp else (-MAX_RISK_USD)
                        bot_state["trade_history"].append({**pos, "exit": curr, "pnl": pnl, "outcome": outcome})
                        del bot_state["active_positions"][sym]
                        
                        send_telegram(f"📢 *POZİSYON KAPANDI*\nParite: `{sym}`\nSonuç: *{outcome}*\nÇıkış Fiyatı: `${curr}`\nNet K/Z: `+${pnl:.2f}`" if pnl > 0 else f"📢 *POZİSYON KAPANDI*\nParite: `{sym}`\nSonuç: *{outcome}*\nÇıkış Fiyatı: `${curr}`\nNet K/Z: `${pnl:.2f}`")
                except Exception as e:
                    pass

            await asyncio.sleep(15)  # Scan interval every 15s
    finally:
        await exchange.close()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(trading_loop())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
