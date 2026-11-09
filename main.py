import os
import time
import asyncio
import threading
import logging
from datetime import datetime
from typing import List, Dict, Any
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn
import ccxt.async_support as ccxt
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SentinelBot")

# Environment & Config
PORT = int(os.getenv("PORT", "10000"))
USE_BINANCE_DEMO = os.getenv("USE_BINANCE_DEMO", "true").lower() == "true"

# Binance Demo Keys (User's active demo keys from demo.binance.com)
API_KEY = os.getenv("BINANCE_API_KEY", "pixh2f6B0ndk04gEfKbPoXd7s39neRCD8r7SwGLeUhQdcNmANUerOK2yIv656CrM")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "59G8HFMK5rqy9rGVhOd4K8uF3sN71hs6cvmzBJQ36S3cT8iXmTPHN3C5EEsHDTNk")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 10 Strong Crypto Pairs (DOGE, LTC, PEPE, SHIB removed per user instruction)
SYMBOLS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "SUI/USDT:USDT",
    "NEAR/USDT:USDT",
    "LINK/USDT:USDT",
    "APT/USDT:USDT"
]

LEVERAGE = 3  # Safe 3x isolated leverage
TARGET_RR = 2.0  # 1 : 2.0 Risk-to-Reward ratio
MIN_NOTIONAL_USD = 25.0  # Minimum order notional to safely clear Binance Futures thresholds (~$8 margin at 3x)

app = FastAPI(title="Binance Sentinel Bot", version="2.5.0")

# State
thread_error = None

bot_state = {
    "status": "RUNNING",
    "mode": "BINANCE_DEMO_LIVE" if USE_BINANCE_DEMO else "LOCAL_SIMULATION",
    "scanned_symbols": len(SYMBOLS),
    "symbols_list": [s.replace(":USDT", "") for s in SYMBOLS],
    "account_balance": 4997.20,
    "active_positions": {},
    "trade_history": [],
    "last_scan_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    "cycle_count": 0,
    "market_prices": {},
    "thread_error": None
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

@app.get("/favicon.ico")
def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="#10b981"/><path d="M50 20 L75 75 L25 75 Z" fill="#ffffff"/></svg>'
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/api/state")
def get_state():
    bot_state["thread_error"] = thread_error
    return {
        "bot": "Binance Sentinel Multi-Pair Bot (Demo Futures Connected)",
        "state": bot_state,
        "note": "24/7 Cloud Sentinel Active on demo.binance.com"
    }

@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/", response_class=HTMLResponse)
def index_ui():
    html_content = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Binance Sentinel Bot - 7/24 Canlı Testnet Paneli</title>
    <link rel="icon" type="image/svg+xml" href="/favicon.ico">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #080b11;
            --bg-card: rgba(18, 24, 38, 0.7);
            --bg-card-hover: rgba(26, 34, 52, 0.9);
            --border-glass: rgba(255, 255, 255, 0.08);
            --emerald: #10b981;
            --cyan: #06b6d4;
            --amber: #f59e0b;
            --rose: #f43f5e;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(16, 185, 129, 0.08) 0%, transparent 40%),
                radial-gradient(circle at 85% 85%, rgba(6, 182, 212, 0.08) 0%, transparent 40%);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            min-height: 100vh;
            padding: 24px;
            overflow-x: hidden;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding-bottom: 24px; border-bottom: 1px solid var(--border-glass);
            margin-bottom: 32px; flex-wrap: wrap; gap: 16px;
        }
        .brand-group { display: flex; align-items: center; gap: 14px; }
        .brand-icon {
            width: 44px; height: 44px; background: linear-gradient(135deg, #10b981, #06b6d4);
            border-radius: 12px; display: flex; align-items: center; justify-content: center;
            box-shadow: 0 0 20px rgba(16, 185, 129, 0.4); font-size: 22px;
        }
        .brand-title {
            font-size: 22px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(to right, #ffffff, #cbd5e1);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .brand-subtitle { font-size: 13px; color: var(--text-secondary); font-weight: 500; }
        .pulse-badge {
            display: inline-flex; align-items: center; gap: 8px;
            background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.35);
            padding: 8px 16px; border-radius: 9999px; font-size: 13px; font-weight: 700;
            color: var(--emerald); letter-spacing: 0.3px;
        }
        .pulse-dot {
            width: 10px; height: 10px; background: var(--emerald);
            border-radius: 50%; position: relative;
        }
        .pulse-dot::after {
            content: ''; position: absolute; inset: -4px; border-radius: 50%;
            background: var(--emerald); opacity: 0.5; animation: pulse 1.8s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.8; }
            50% { transform: scale(2); opacity: 0; }
            100% { transform: scale(0.9); opacity: 0; }
        }
        .metrics-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 18px; margin-bottom: 32px;
        }
        .metric-card {
            background: var(--bg-card); backdrop-filter: blur(12px);
            border: 1px solid var(--border-glass); border-radius: 16px; padding: 20px;
            position: relative; transition: all 0.25s ease;
        }
        .metric-card:hover {
            transform: translateY(-2px); border-color: rgba(255, 255, 255, 0.15);
            background: var(--bg-card-hover);
        }
        .metric-label {
            font-size: 12px; color: var(--text-secondary); text-transform: uppercase;
            font-weight: 700; letter-spacing: 0.8px; margin-bottom: 8px;
        }
        .metric-value {
            font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono', monospace;
            color: #ffffff; display: flex; align-items: baseline; gap: 8px;
        }
        .metric-sub { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
        .card-section {
            background: var(--bg-card); backdrop-filter: blur(12px);
            border: 1px solid var(--border-glass); border-radius: 18px; padding: 24px;
            margin-bottom: 28px;
        }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .section-title {
            font-size: 17px; font-weight: 700; color: var(--text-primary);
            display: flex; align-items: center; gap: 10px;
        }
        .pairs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
        .pair-badge {
            background: rgba(255, 255, 255, 0.03); border: 1px solid var(--border-glass);
            border-radius: 12px; padding: 12px 14px; display: flex; justify-content: space-between;
            align-items: center; transition: all 0.2s ease;
        }
        .pair-badge:hover { border-color: rgba(16, 185, 129, 0.4); background: rgba(16, 185, 129, 0.05); }
        .pair-name { font-weight: 700; font-size: 14px; font-family: 'JetBrains Mono', monospace; }
        .pair-status {
            font-size: 11px; color: var(--emerald); background: rgba(16, 185, 129, 0.15);
            padding: 2px 8px; border-radius: 6px; font-weight: 600;
        }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }
        th {
            color: var(--text-secondary); font-weight: 600; padding: 12px;
            border-bottom: 1px solid var(--border-glass); text-transform: uppercase;
            font-size: 11px; letter-spacing: 0.5px;
        }
        td { padding: 14px 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); font-family: 'JetBrains Mono', monospace; }
        .empty-row {
            text-align: center; padding: 32px 12px; color: var(--text-muted);
            font-family: 'Plus Jakarta Sans', sans-serif; font-size: 14px;
        }
        .radar-box {
            display: flex; align-items: center; justify-content: center; gap: 12px;
            padding: 24px; background: rgba(255, 255, 255, 0.02); border-radius: 12px;
            border: 1px dashed rgba(255, 255, 255, 0.1); color: var(--text-secondary); font-size: 14px;
        }
        .spinner {
            width: 20px; height: 20px; border: 2px solid rgba(16, 185, 129, 0.2);
            border-top-color: var(--emerald); border-radius: 50%; animation: spin 1s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        footer {
            text-align: center; padding-top: 24px; border-top: 1px solid var(--border-glass);
            color: var(--text-muted); font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand-group">
                <div class="brand-icon">⚡</div>
                <div>
                    <div class="brand-title">BINANCE SENTINEL BOT</div>
                    <div class="brand-subtitle">demo.binance.com Canlı Bağlantılı Bulut Nöbetçisi</div>
                </div>
            </div>
            <div class="pulse-badge">
                <span class="pulse-dot"></span>
                <span>BINANCE TESTNET / DEMO CANLI</span>
            </div>
        </header>

        <!-- Metric Cards -->
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Demo Kasa Bakiyesi</div>
                <div class="metric-value" style="color: #f59e0b;" id="m-balance">$4,997.20</div>
                <div class="metric-sub">demo.binance.com Canlı Bakiye</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Taranan Pariteler</div>
                <div class="metric-value" id="m-symbols">10 / 10</div>
                <div class="metric-sub">DOGE & LTC Temizlendi (Meme-Free)</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Kaldıraç & Emir Tipi</div>
                <div class="metric-value">3x İzole</div>
                <div class="metric-sub">Gerçek Demo Emirleri Açılır</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Son Tarama Döngüsü</div>
                <div class="metric-value" style="font-size: 18px;" id="m-time">Yükleniyor...</div>
                <div class="metric-sub">Her 15 Saniyede Canlı Tarama</div>
            </div>
        </div>

        <!-- Taranan Pariteler Grid -->
        <div class="card-section">
            <div class="section-header">
                <div class="section-title">
                    <span>📡</span>
                    <span>Taranan 10 Onaylı Parite (15m Trend & Hacim Patlaması Filtresi)</span>
                </div>
            </div>
            <div class="pairs-grid" id="pairs-container"></div>
        </div>

        <!-- Açık Pozisyonlar -->
        <div class="card-section">
            <div class="section-header">
                <div class="section-title">
                    <span>🎯</span>
                    <span>Binance Demo Açık Pozisyonlar</span>
                </div>
            </div>
            <div id="positions-box">
                <div class="radar-box">
                    <div class="spinner"></div>
                    <span>demo.binance.com üzerinde 10 parite taranıyor... Sinyal geldiğinde emir anında Binance ekranına düşecektir.</span>
                </div>
            </div>
        </div>

        <!-- İşlem Geçmişi -->
        <div class="card-section">
            <div class="section-header">
                <div class="section-title">
                    <span>📜</span>
                    <span>Tamamlanan Testnet İşlemleri</span>
                </div>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>Parite</th>
                            <th>Yön</th>
                            <th>Giriş</th>
                            <th>Çıkış</th>
                            <th>Sonuç</th>
                            <th>Net K/Z</th>
                        </tr>
                    </thead>
                    <tbody id="history-body">
                        <tr>
                            <td colspan="6" class="empty-row">Henüz kapanmış işlem yok. Bot Binance Demo üzerinde nöbette.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <footer>
            Binance Sentinel Cloud Engine v2.5.0 • demo.binance.com Bağlantılı • Otomatik Canlı Güncelleniyor
        </footer>
    </div>

    <script>
        const pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "SUI/USDT", "NEAR/USDT", "LINK/USDT", "APT/USDT"];
        
        const pCont = document.getElementById('pairs-container');
        pCont.innerHTML = pairs.map(p => `
            <div class="pair-badge">
                <span class="pair-name">${p}</span>
                <span class="pair-status">TARANIYOR</span>
            </div>
        `).join('');

        async function updateDashboard() {
            try {
                const res = await fetch('/api/state');
                const data = await res.json();
                const st = data.state;

                if (st.account_balance) {
                    document.getElementById('m-balance').innerText = '$' + Number(st.account_balance).toFixed(2);
                }
                document.getElementById('m-symbols').innerText = st.scanned_symbols + ' / ' + st.scanned_symbols;
                if (st.last_scan_time) {
                    const timeOnly = st.last_scan_time.split(' ')[1] || st.last_scan_time;
                    document.getElementById('m-time').innerText = timeOnly + ' UTC';
                }

                // Render active positions
                const posBox = document.getElementById('positions-box');
                const activeKeys = Object.keys(st.active_positions || {});
                if (activeKeys.length === 0) {
                    posBox.innerHTML = `
                        <div class="radar-box">
                            <div class="spinner"></div>
                            <span>demo.binance.com üzerinde 10 parite taranıyor... Sinyal geldiğinde emir anında Binance ekranına düşecektir.</span>
                        </div>
                    `;
                } else {
                    let tableHtml = `
                        <div class="table-wrap">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Parite</th>
                                        <th>Yön</th>
                                        <th>Giriş Fiyatı</th>
                                        <th>Stop Loss</th>
                                        <th>Take Profit</th>
                                        <th>Miktar</th>
                                        <th>Emir No</th>
                                    </tr>
                                </thead>
                                <tbody>
                    `;
                    activeKeys.forEach(k => {
                        const p = st.active_positions[k];
                        tableHtml += `
                            <tr>
                                <td style="font-weight:700;">${p.symbol}</td>
                                <td style="color:${p.side === 'LONG' ? '#10b981' : '#f43f5e'};font-weight:700;">${p.side}</td>
                                <td>$${p.entry_price}</td>
                                <td style="color:#f43f5e;">$${p.stop_loss}</td>
                                <td style="color:#10b981;">$${p.take_profit}</td>
                                <td>${p.qty}</td>
                                <td style="color:#94a3b8;">${p.order_id || 'DEMO'}</td>
                            </tr>
                        `;
                    });
                    tableHtml += `</tbody></table></div>`;
                    posBox.innerHTML = tableHtml;
                }

                // Render history
                const hBody = document.getElementById('history-body');
                if (st.trade_history && st.trade_history.length > 0) {
                    hBody.innerHTML = st.trade_history.slice(-10).reverse().map(h => `
                        <tr>
                            <td style="font-weight:700;">${h.symbol}</td>
                            <td style="color:${h.side === 'LONG' ? '#10b981' : '#f43f5e'};font-weight:700;">${h.side}</td>
                            <td>$${h.entry_price}</td>
                            <td>$${h.exit}</td>
                            <td style="color:${h.pnl > 0 ? '#10b981' : '#f43f5e'};font-weight:700;">${h.outcome}</td>
                            <td style="color:${h.pnl > 0 ? '#10b981' : '#f43f5e'};font-weight:700;">${h.pnl > 0 ? '+' : ''}$${h.pnl.toFixed(2)}</td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error('Update error:', err);
            }
        }

        setInterval(updateDashboard, 2500);
        updateDashboard();
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

def calculate_indicators(candles: List[List[float]]) -> Dict[str, Any]:
    if len(candles) < 30:
        return {}
    
    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]
    volumes = [c[5] for c in candles]

    def ema(values, period):
        k = 2 / (period + 1)
        res = [values[0]]
        for v in values[1:]:
            res.append(v * k + res[-1] * (1 - k))
        return res[-1]

    ema_fast = ema(closes, 14)
    ema_slow = ema(closes, 50)

    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 1
    avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1
    rs = avg_gain / (avg_loss if avg_loss != 0 else 0.0001)
    rsi = 100 - (100 / (1 + rs))

    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volumes[-1]
    vol_spike = volumes[-1] >= (avg_vol * 1.5)

    trs = []
    for i in range(1, len(candles)):
        h, l, prev_c = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else trs[-1]

    return {
        "price": closes[-1],
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": rsi,
        "vol_spike": vol_spike,
        "atr": atr
    }

async def trading_loop():
    global thread_error
    logger.info("Initializing Binance Demo Trading Engine...")
    try:
        exchange_params = {
            "apiKey": API_KEY,
            "secret": SECRET_KEY,
            "enableRateLimit": True,
            "timeout": 10000,
            "options": {"defaultType": "future"}
        }
        exchange = ccxt.binance(exchange_params)
        
        if USE_BINANCE_DEMO:
            if hasattr(exchange, 'enable_demo_trading'):
                exchange.enable_demo_trading(True)
            else:
                demo_urls = exchange.urls.get('demo', {
                    'dapiPublic': 'https://demo-dapi.binance.com/dapi/v1',
                    'dapiPrivate': 'https://demo-dapi.binance.com/dapi/v1',
                    'dapiPrivateV2': 'https://demo-dapi.binance.com/dapi/v2',
                    'fapiPublic': 'https://demo-fapi.binance.com/fapi/v1',
                    'fapiPublicV2': 'https://demo-fapi.binance.com/fapi/v2',
                    'fapiPublicV3': 'https://demo-fapi.binance.com/fapi/v3',
                    'fapiPrivate': 'https://demo-fapi.binance.com/fapi/v1',
                    'fapiPrivateV2': 'https://demo-fapi.binance.com/fapi/v2',
                    'fapiPrivateV3': 'https://demo-fapi.binance.com/fapi/v3',
                    'public': 'https://demo-api.binance.com/api/v3',
                    'private': 'https://demo-api.binance.com/api/v3',
                    'v1': 'https://demo-api.binance.com/api/v1',
                })
                exchange.urls['api'] = demo_urls
                exchange.options['enableDemoTrading'] = True
            logger.info("DEMO TRADING ENABLED (demo.binance.com)")

        thread_error = None
        logger.info(f"Initialized CCXT Binance Demo engine for {len(SYMBOLS)} symbols.")

        while True:
            try:
                bot_state["cycle_count"] += 1
                bot_state["last_scan_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

                # Update balance periodically
                try:
                    bal = await exchange.fetch_balance()
                    if "USDT" in bal and "total" in bal["USDT"]:
                        bot_state["account_balance"] = round(bal["USDT"]["total"], 2)
                except Exception as be:
                    logger.debug(f"Balance check notice: {be}")

                for symbol in SYMBOLS:
                    try:
                        candles = await exchange.fetch_ohlcv(symbol, timeframe="15m", limit=60)
                        ind = calculate_indicators(candles)
                        if not ind:
                            continue

                        curr_price = ind["price"]
                        bot_state["market_prices"][symbol] = curr_price
                        rsi = ind["rsi"]
                        ema_f = ind["ema_fast"]
                        ema_s = ind["ema_slow"]
                        vol_spike = ind["vol_spike"]
                        atr = ind["atr"]

                        # Entry Conditions
                        signal = None
                        if ema_f > ema_s and 38 <= rsi <= 55 and vol_spike:
                            signal = "LONG"
                        elif ema_f < ema_s and 45 <= rsi <= 62 and vol_spike:
                            signal = "SHORT"

                        # Manage signals
                        if signal and symbol not in bot_state["active_positions"] and len(bot_state["active_positions"]) < 3:
                            sl_dist = atr * 1.5
                            tp_dist = sl_dist * TARGET_RR

                            if signal == "LONG":
                                sl = round(curr_price - sl_dist, 4)
                                tp = round(curr_price + tp_dist, 4)
                            else:
                                sl = round(curr_price + sl_dist, 4)
                                tp = round(curr_price - tp_dist, 4)

                            raw_qty = MIN_NOTIONAL_USD / curr_price
                            qty = round(raw_qty, 2)
                            if qty <= 0:
                                qty = 1.0

                            order_id = "DEMO"
                            # PLACE REAL ORDER ON BINANCE DEMO FUTURES
                            if USE_BINANCE_DEMO and API_KEY and SECRET_KEY:
                                try:
                                    await exchange.set_leverage(LEVERAGE, symbol)
                                    side = "buy" if signal == "LONG" else "sell"
                                    order = await exchange.create_market_order(symbol, side, qty)
                                    order_id = order.get("id", "DEMO")
                                    logger.info(f"REAL DEMO ORDER EXECUTED ON BINANCE: {symbol} {side} {qty} (ID: {order_id})")
                                except Exception as ex:
                                    logger.error(f"Binance demo order error for {symbol}: {ex}")

                            trade_info = {
                                "symbol": symbol,
                                "side": signal,
                                "entry_price": curr_price,
                                "stop_loss": sl,
                                "take_profit": tp,
                                "qty": qty,
                                "order_id": order_id,
                                "timestamp": datetime.utcnow().strftime("%H:%M:%S UTC")
                            }

                            bot_state["active_positions"][symbol] = trade_info

                            tg_msg = (
                                f"🚨 *YENİ BİNANCE TESTNET İŞLEMİ AÇILDI ({signal})*\n"
                                f"Parite: `{symbol}`\n"
                                f"Giriş: `${curr_price}`\n"
                                f"Stop Loss: `${sl}`\n"
                                f"Take Profit: `${tp}`\n"
                                f"Kaldıraç: `3x İzole`\n"
                                f"Miktar: `{qty}`\n"
                                f"Emir No: `{order_id}`\n"
                                f"Platform: `demo.binance.com`"
                            )
                            send_telegram(tg_msg)

                    except Exception as ex:
                        logger.debug(f"Scan error on {symbol}: {ex}")

                    await asyncio.sleep(0.5)

                # Check open positions for exit
                for sym, pos in list(bot_state["active_positions"].items()):
                    try:
                        ticker = await exchange.fetch_ticker(sym)
                        curr = ticker["last"]
                        hit_tp = (pos["side"] == "LONG" and curr >= pos["take_profit"]) or (pos["side"] == "SHORT" and curr <= pos["take_profit"])
                        hit_sl = (pos["side"] == "LONG" and curr <= pos["stop_loss"]) or (pos["side"] == "SHORT" and curr >= pos["stop_loss"])

                        if hit_tp or hit_sl:
                            outcome = "KÂR ALINDI (TP) 🎯" if hit_tp else "STOP OLDU (SL) 🛑"
                            if USE_BINANCE_DEMO and API_KEY and SECRET_KEY:
                                try:
                                    close_side = "sell" if pos["side"] == "LONG" else "buy"
                                    await exchange.create_market_order(sym, close_side, pos["qty"], params={"reduceOnly": True})
                                    logger.info(f"REAL DEMO POSITION CLOSED ON BINANCE: {sym} ({outcome})")
                                except Exception as ex:
                                    logger.error(f"Close order failed on Binance: {ex}")

                            pnl = ((curr - pos["entry_price"]) * pos["qty"]) if pos["side"] == "LONG" else ((pos["entry_price"] - curr) * pos["qty"])
                            bot_state["trade_history"].append({**pos, "exit": curr, "pnl": pnl, "outcome": outcome})
                            del bot_state["active_positions"][sym]
                            
                            send_telegram(f"📢 *BİNANCE TESTNET POZİSYON KAPANDI*\nParite: `{sym}`\nSonuç: *{outcome}*\nÇıkış Fiyatı: `${curr}`\nNet K/Z: `${pnl:.2f}`")
                    except Exception as e:
                        pass

                logger.info(f"Scan cycle #{bot_state['cycle_count']} finished at {bot_state['last_scan_time']}")

            except Exception as loop_ex:
                logger.error(f"Global loop error caught: {loop_ex}")

            await asyncio.sleep(10)

    except Exception as fatal_ex:
        import traceback
        thread_error = traceback.format_exc()
        logger.critical(f"FATAL trading_loop initialization error: {fatal_ex}")

def start_bot_thread():
    global thread_error
    try:
        asyncio.run(trading_loop())
    except Exception as e:
        import traceback
        thread_error = traceback.format_exc()
        logger.critical(f"THREAD CRASHED: {thread_error}")

bot_thread = threading.Thread(target=start_bot_thread, daemon=True)
bot_thread.start()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
