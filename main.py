import time
import os
import sqlite3
import hmac
import hashlib
import logging
import math
import json
import websocket
import requests
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode
from flask import Flask

# إعداد الـ Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("mexc_elite_pro_v2.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# 1️⃣ خادم Flask للإبقاء على البوت 24/7
app = Flask(__name__)

@app.route('/')
def health_check():
    return "MEXC Institutional-Grade Elite Pro Trading Bot (v2) is Running 24/7!", 200

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# 2️⃣ إعدادات البيئة (تدعم متغيرات البيئة أو القيم الافتراضية)
TELEGRAM_BOT_TOKEN = os.environ.get("BOT_TOKEN", "8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0")
CHAT_ID = os.environ.get("CHAT_ID", "1015963752")

MEXC_API_KEY = os.environ.get("MEXC_API_KEY", "your_mexc_api_key")
MEXC_SECRET_KEY = os.environ.get("MEXC_SECRET_KEY", "your_mexc_secret_key")
BASE_URL = "https://api.mexc.com"

http_session = requests.Session()

# 3️⃣ قاعدة بيانات SQLite متقدمة وآمنة Thread-Safe
DB_FILE = "mexc_pro_bot_v2.db"
db_lock = Lock()

def init_db():
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_trades (
                symbol TEXT PRIMARY KEY,
                entry_time REAL,
                real_entry_price REAL,
                quantity REAL,
                initial_quantity REAL,
                invested_usdt REAL,
                accumulated_fees REAL,
                realized_usdt REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                tp1_hit INTEGER,
                tp2_hit INTEGER,
                sl REAL,
                highest_price REAL,
                closing INTEGER,
                atr REAL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                entry_time REAL,
                exit_time REAL,
                fees REAL,
                roi REAL,
                duration_mins REAL,
                reason_exit TEXT,
                pnl_usdt REAL,
                win INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

init_db()

def db_get_state(key, default_val):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except:
                return row[0]
    return default_val

def db_set_state(key, val):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, json.dumps(val)))
        conn.commit()

traded_symbols_time = db_get_state("traded_symbols_time", {})
blacklist_symbols = db_get_state("blacklist_symbols", {})
last_trade_close_time = db_get_state("last_trade_close_time", 0.0)
consecutive_losses = db_get_state("consecutive_losses", 0)
loss_lockout_until = db_get_state("loss_lockout_until", 0.0)

MAX_CONCURRENT_TRADES = 3
COOLDOWN_SECONDS = 180
MAX_CONSECUTIVE_LOSSES = 3
LOSS_LOCKOUT_SECONDS = 3600
TRADING_FEE_RATE = float(os.environ.get("TRADING_FEE_RATE", "0.001"))

api_error_count = 0
circuit_breaker_until = 0.0
realtime_prices = {}
prices_lock = Lock()

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        http_session.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.error(f"خطأ تليجرام: {e}")

def get_mexc_signature(query_string):
    return hmac.new(MEXC_SECRET_KEY.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

def public_request(endpoint, params=None, max_retries=3):
    global api_error_count, circuit_breaker_until
    if time.time() < circuit_breaker_until:
        return None
    
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(max_retries):
        try:
            res = http_session.get(url, params=params, timeout=5)
            if res.status_code == 200:
                api_error_count = 0
                return res.json()
            elif res.status_code == 429:
                time.sleep(2 * (attempt + 1))
        except Exception as e:
            time.sleep(1)
            
    api_error_count += 1
    if api_error_count >= 5:
        circuit_breaker_until = time.time() + 60
        logging.warning("🚨 تفعيل Circuit Breaker لمدة دقيقة بسبب تكرار أخطاء الـ API.")
        api_error_count = 0
    return None

def mexc_private_request(method, endpoint, params=None, max_retries=3):
    if params is None:
        params = {}
    for attempt in range(max_retries):
        try:
            params['timestamp'] = int(time.time() * 1000)
            query_string = urlencode(params)
            signature = get_mexc_signature(query_string)
            url = f"{BASE_URL}{endpoint}?{query_string}&signature={signature}"
            headers = {'X-MEXC-APIKEY': MEXC_API_KEY}
            
            if method == 'GET':
                res = http_session.get(url, headers=headers, timeout=6)
            elif method == 'POST':
                res = http_session.post(url, headers=headers, timeout=6)
            else:
                return None
            
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 429:
                time.sleep(2 * (attempt + 1))
        except Exception as e:
            time.sleep(1)
    return None

def get_account_balance_usdt():
    res = mexc_private_request('GET', '/api/v3/account')
    if res and 'balances' in res:
        for b in res['balances']:
            if b['asset'] == 'USDT':
                return float(b['free'])
    return 0.0

# 📊 المؤشرات الفنية المتقدمة
def calculate_ema_series(prices, period):
    if len(prices) < period:
        return []
    multiplier = 2 / (period + 1)
    ema_list = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema = (price - ema_list[-1]) * multiplier + ema_list[-1]
        ema_list.append(ema)
    return ema_list

def calculate_macd_recent(prices, max_lookback=3):
    if len(prices) < 40:
        return False
    ema12_full = calculate_ema_series(prices, 12)
    ema26_full = calculate_ema_series(prices, 26)
    diff_len = len(ema12_full) - len(ema26_full)
    ema12_aligned = ema12_full[diff_len:]
    
    macd_line = [e12 - e26 for e12, e26 in zip(ema12_aligned, ema26_full)]
    if len(macd_line) < 12:
        return False
    signal_line = calculate_ema_series(macd_line, 9)
    if len(signal_line) < max_lookback + 1:
        return False
        
    for i in range(1, max_lookback + 1):
        idx = -i
        if macd_line[idx-1] < signal_line[idx-1] and macd_line[idx] > signal_line[idx]:
            return True
    return False

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        if change > 0: gains += change
        else: losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(prices)):
        change = prices[i] - prices[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return closes[-1] * 0.02
    tr_list = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hc, lc))
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else closes[-1] * 0.02
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr

def calculate_vwap(highs, lows, closes, volumes):
    if not highs or not lows or not closes or not volumes:
        return 0.0
    cum_pv = 0.0
    cum_vol = 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        typical_price = (h + l + c) / 3.0
        cum_pv += typical_price * v
        cum_vol += v
    return (cum_pv / cum_vol) if cum_vol > 0 else closes[-1]

def calculate_obv(closes, volumes):
    if len(closes) < 2:
        return 0
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv[-1] - obv[-5] if len(obv) >= 5 else 0

def check_order_book_imbalance(symbol):
    depth = public_request('/api/v3/depth', {"symbol": symbol, "limit": 20})
    if not depth or 'bids' not in depth or 'asks' not in depth:
        return True
    bid_vol = sum(float(b[1]) for b in depth['bids'])
    ask_vol = sum(float(a[1]) for a in depth['asks'])
    if ask_vol <= 0:
        return True
    ratio = bid_vol / ask_vol
    return ratio >= 1.5

exchange_info_cache = {}
last_exchange_info_fetch = 0
klines_cache = {}
last_klines_cache_fetch = 0

def get_exchange_info_cached(symbol):
    global exchange_info_cache, last_exchange_info_fetch
    current_time = time.time()
    if not exchange_info_cache or (current_time - last_exchange_info_fetch) > 3600:
        data = public_request('/api/v3/exchangeInfo')
        if data:
            temp_cache = {}
            for s in data.get("symbols", []):
                sym = s.get("symbol")
                info = {"stepSize": 0.0001, "minNotional": 5.0, "quoteOrderQtyMarketAllowed": False}
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        info["stepSize"] = float(f.get("stepSize", "0.0001"))
                    elif f.get("filterType") in ["MIN_NOTIONAL", "NOTIONAL"]:
                        info["minNotional"] = float(f.get("minNotional", 5.0) or f.get("minNotionalValue", 5.0))
                if s.get("quoteOrderQtyMarketAllowed"):
                    info["quoteOrderQtyMarketAllowed"] = True
                temp_cache[sym] = info
            exchange_info_cache = temp_cache
            last_exchange_info_fetch = current_time
    return exchange_info_cache.get(symbol, {"stepSize": 0.0001, "minNotional": 5.0, "quoteOrderQtyMarketAllowed": False})

def floor_to_step_size(quantity, step_size):
    if step_size <= 0:
        return quantity
    precision = round(-math.log10(step_size)) if step_size < 1 else 0
    precision = max(0, precision)
    factor = 10.0 ** precision
    return math.floor(quantity * factor) / factor

def check_market_trend_1h():
    klines = public_request('/api/v3/klines', {"symbol": "BTCUSDT", "interval": "1h", "limit": 220})
    if klines and len(klines) >= 200:
        closes = [float(k[4]) for k in klines]
        ema200 = calculate_ema_series(closes, 200)
        if ema200 and closes[-1] < ema200[-1]:
            return False
    return True

def get_cached_klines_15m(symbol):
    global klines_cache, last_klines_cache_fetch
    current_time = time.time()
    if current_time - last_klines_cache_fetch > 30:
        klines_cache.clear()
        last_klines_cache_fetch = current_time
        
    if symbol in klines_cache:
        return klines_cache[symbol]
        
    klines = public_request('/api/v3/klines', {"symbol": symbol, "interval": "15m", "limit": 220})
    if klines:
        klines_cache[symbol] = klines
    return klines

def analyze_single_pair(symbol, current_time_epoch):
    try:
        if symbol in traded_symbols_time and (current_time_epoch - traded_symbols_time[symbol]) < 86400:
            return None
            
        if symbol in blacklist_symbols and current_time_epoch < blacklist_symbols[symbol]:
            return None

        klines = get_cached_klines_15m(symbol)
        if not klines or len(klines) < 200:
            return None
            
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        
        last_price = closes[-1]
        
        ema50 = calculate_ema_series(closes, 50)
        ema200 = calculate_ema_series(closes, 200)
        if not ema50 or not ema200 or ema50[-1] <= ema200[-1]:
            return None
            
        vwap = calculate_vwap(highs, lows, closes, volumes)
        if last_price < vwap:
            return None
            
        if not calculate_macd_recent(closes, max_lookback=3):
            return None
            
        rsi = calculate_rsi(closes, 14)
        if not (50 <= rsi <= 75):
            return None
            
        obv_trend = calculate_obv(closes, volumes)
        if obv_trend <= 0:
            return None
            
        if not check_order_book_imbalance(symbol):
            return None
            
        atr_val = calculate_atr(highs, lows, closes, 14)
        score = int(min(100, max(60, (rsi * 0.4) + (60 if obv_trend > 0 else 40))))
        
        return {
            "symbol": symbol,
            "price": last_price,
            "score": score,
            "atr": atr_val
        }
    except Exception as e:
        return None

def scan_all_spot_pairs():
    opportunities = []
    current_time_epoch = time.time()
    
    if not check_market_trend_1h():
        return opportunities

    tickers = public_request('/api/v3/ticker/24hr')
    if not tickers:
        return opportunities
        
    target_symbols = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base_asset = symbol[:-4]
        if base_asset in ["USDC", "FDUSD", "TUSD", "DAI"] or "UP" in base_asset or "DOWN" in base_asset:
            continue
        change_24h = float(t.get("priceChangePercent", 0) or 0)
        if change_24h < 2.0:
            continue
        if float(t.get("quoteVolume", 0) or 0) < 3000000:
            continue
        target_symbols.append(symbol)
        
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(analyze_single_pair, sym, current_time_epoch): sym for sym in target_symbols}
        for future in as_completed(futures):
            res = future.result()
            if res:
                opportunities.append(res)
                
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    return opportunities

def execute_spot_buy(symbol, usdt_amount, max_retries=3):
    endpoint = "/api/v3/order"
    info = get_exchange_info_cached(symbol)
    
    p_res = public_request('/api/v3/ticker/price', {"symbol": symbol})
    if not p_res:
        return False, 0.0, 0.0, 0.0, None
    pre_price = float(p_res.get("price", 0))
    if pre_price <= 0:
        return False, 0.0, 0.0, 0.0, None

    params = {"symbol": symbol, "side": "BUY", "type": "MARKET"}
    if info["quoteOrderQtyMarketAllowed"]:
        params["quoteOrderQty"] = round(usdt_amount, 2)
    else:
        params["quantity"] = floor_to_step_size(usdt_amount / pre_price, info["stepSize"])

    for attempt in range(max_retries):
        res = mexc_private_request('POST', endpoint, params)
        if res and res.get('status') in ['FILLED', 'PARTIALLY_FILLED']:
            exec_qty = float(res.get('executedQty', 0) or 0)
            cumm_quote = float(res.get('cummulativeQuoteQty', usdt_amount) or usdt_amount)
            real_entry = cumm_quote / exec_qty if exec_qty > 0 else pre_price
            return True, exec_qty, cumm_quote, real_entry, res.get('orderId')
        elif res and 'orderId' in res:
            time.sleep(0.3)
            check_order = mexc_private_request('GET', '/api/v3/order', {'symbol': symbol, 'orderId': res.get('orderId')})
            if check_order and check_order.get('status') == 'FILLED':
                exec_qty = float(check_order.get('executedQty', 0) or 0)
                cumm_quote = float(check_order.get('cummulativeQuoteQty', usdt_amount) or usdt_amount)
                real_entry = cumm_quote / exec_qty if exec_qty > 0 else pre_price
                return True, exec_qty, cumm_quote, real_entry, res.get('orderId')
        time.sleep(1)
        
    return False, 0.0, 0.0, 0.0, None

def execute_spot_sell(symbol, quantity, max_retries=3):
    endpoint = "/api/v3/order"
    info = get_exchange_info_cached(symbol)
    qty = floor_to_step_size(quantity, info["stepSize"])
    params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty}
    
    for attempt in range(max_retries):
        res = mexc_private_request('POST', endpoint, params)
        if res and res.get('status') in ['FILLED', 'PARTI_FILLED']:
            exec_qty = float(res.get('executedQty', 0) or 0)
            cumm_quote = float(res.get('cummulativeQuoteQty', 0) or 0)
            avg_exit = cumm_quote / exec_qty if exec_qty > 0 else 0.0
            return True, exec_qty, cumm_quote, avg_exit, res.get('orderId')
        elif res and 'orderId' in res:
            return True, qty, 0.0, 0.0, res.get('orderId')
        time.sleep(1)
    return False, 0.0, 0.0, 0.0, None

def on_message(ws, message):
    try:
        data = json.loads(message)
        if "s" in data and "p" in data:
            symbol = data["s"]
            price = float(data["p"])
            with prices_lock:
                realtime_prices[symbol] = price
    except:
        pass

def start_websocket():
    while True:
        try:
            socket_url = "wss://wbs.mexc.com/ws"
            ws = websocket.WebSocketApp(socket_url, on_message=on_message)
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            time.sleep(5)

def get_db_active_trades():
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_trades")
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

def update_db_trade(trade):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE active_trades SET 
                quantity = ?, accumulated_fees = ?, realized_usdt = ?, 
                tp1_hit = ?, tp2_hit = ?, sl = ?, highest_price = ?, closing = ?
            WHERE symbol = ?
        """, (
            trade["quantity"], trade["accumulated_fees"], trade["realized_usdt"],
            int(trade["tp1_hit"]), int(trade["tp2_hit"]), trade["sl"], 
            trade["highest_price"], int(trade["closing"]), trade["symbol"]
        ))
        conn.commit()

def remove_db_trade(symbol):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM active_trades WHERE symbol = ?", (symbol,))
        conn.commit()

def add_db_trade(trade):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO active_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["symbol"], trade["entry_time"], trade["real_entry_price"],
            trade["quantity"], trade["initial_quantity"], trade["invested_usdt"],
            trade["accumulated_fees"], trade["realized_usdt"], trade["tp1"],
            trade["tp2"], trade["tp3"], int(trade["tp1_hit"]), int(trade["tp2_hit"]),
            trade["sl"], trade["highest_price"], int(trade["closing"]), trade["atr"]
        ))
        conn.commit()

def add_db_history(history_item):
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_history (symbol, entry_time, exit_time, fees, roi, duration_mins, reason_exit, pnl_usdt, win)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            history_item["symbol"], history_item["entry_time"], history_item["exit_time"],
            history_item["fees"], history_item["roi"], history_item["duration_mins"],
            history_item["reason_exit"], history_item["pnl_usdt"], int(history_item["win"])
        ))
        conn.commit()

def check_and_execute_trades():
    global last_trade_close_time, loss_lockout_until
    
    if time.time() < loss_lockout_until:
        return
    active_trades = get_db_active_trades()
    if len(active_trades) >= MAX_CONCURRENT_TRADES:
        return
    if (time.time() - last_trade_close_time) < COOLDOWN_SECONDS:
        return
        
    usdt_balance = get_account_balance_usdt()
    opportunities = scan_all_spot_pairs()
    
    for opp in opportunities:
        symbol = opp["symbol"]
        score = opp["score"]
        atr = opp["atr"]
        
        if any(t["symbol"] == symbol for t in active_trades):
            continue
            
        if score >= 90:
            alloc_pct = 0.04
        elif score >= 80:
            alloc_pct = 0.03
        else:
            alloc_pct = 0.02
            
        trade_allocation = usdt_balance * alloc_pct
        if trade_allocation < 5.0:
            continue
            
        success, executed_qty, invested_cumm, real_entry_price, order_id = execute_spot_buy(symbol, trade_allocation)
        if not success or executed_qty <= 0 or real_entry_price <= 0:
            continue
            
        buy_fee = invested_cumm * TRADING_FEE_RATE
        atr_pct = atr / real_entry_price
        sl_pct = max(0.015, min(atr_pct * 1.5, 0.04))
        tp_pct = sl_pct * 2.0
        
        dynamic_sl = real_entry_price * (1 - sl_pct)
        tp1 = real_entry_price * (1 + tp_pct * 0.5)
        tp2 = real_entry_price * (1 + tp_pct * 0.75)
        tp3 = real_entry_price * (1 + tp_pct)
        
        new_trade = {
            "symbol": symbol,
            "entry_time": time.time(),
            "real_entry_price": real_entry_price,
            "quantity": executed_qty,
            "initial_quantity": executed_qty,
            "invested_usdt": invested_cumm,
            "accumulated_fees": buy_fee,
            "realized_usdt": 0.0,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "tp1_hit": False,
            "tp2_hit": False,
            "sl": dynamic_sl,
            "highest_price": real_entry_price,
            "closing": False,
            "atr": atr
        }
        
        add_db_trade(new_trade)
        traded_symbols_time[symbol] = time.time()
        db_set_state("traded_symbols_time", traded_symbols_time)
        
        msg = (
            f"🟢 *صفقة احترافية جديدة (VWAP + OBV + ATR Trailing)!*\n"
            f"-----------------------------------\n"
            f"🪙 *الزوج:* `{symbol}` | ⭐ *التقييم:* `{score}/100`\n"
            f"💵 *سعر التنفيذ الحقيقي:* `${real_entry_price:,.4f}` | 📦 *الكمية:* `{executed_qty}`\n"
            f"🎯 *الأهداف:* TP1: `{tp1:,.4f}` | TP2: `{tp2:,.4f}` | TP3: `{tp3:,.4f}`\n"
            f"🛡️ *وقف الخسارة الأولي:* `${dynamic_sl:,.4f}`\n"
            f"💰 *المبلغ المستثمر:* `${invested_cumm:.2f} ({alloc_pct*100}% الرصيد)`"
        )
        send_telegram_alert(msg)
        break

def update_active_trades():
    global last_trade_close_time, consecutive_losses, loss_lockout_until
    active_trades = get_db_active_trades()
    
    for trade in active_trades:
        if trade["closing"]:
            continue
            
        symbol = trade["symbol"]
        with prices_lock:
            current_price = realtime_prices.get(symbol, 0.0)
            
        if current_price <= 0:
            p_res = public_request('/api/v3/ticker/price', {"symbol": symbol})
            if p_res:
                current_price = float(p_res.get("price", 0) or 0)
        if current_price <= 0:
            continue
            
        atr_val = trade.get("atr", current_price * 0.02)
        new_sl = current_price - (atr_val * 2.0)
        if current_price > trade["highest_price"]:
            trade["highest_price"] = current_price
            if new_sl > trade["sl"]:
                trade["sl"] = new_sl
                update_db_trade(trade)
                
        if current_price >= trade["tp3"]:
            trade["closing"] = True
            update_db_trade(trade)
            
            success, sold_qty, return_val, avg_exit, _ = execute_spot_sell(symbol, trade["quantity"])
            if not success:
                trade["closing"] = False
                update_db_trade(trade)
                continue
                
            sell_fee = return_val * TRADING_FEE_RATE
            trade["accumulated_fees"] += sell_fee
            trade["realized_usdt"] += return_val
            
            total_pnl = trade["realized_usdt"] - trade["invested_usdt"] - trade["accumulated_fees"]
            roi = (total_pnl / trade["invested_usdt"]) * 100
            duration_mins = (time.time() - trade["entry_time"]) / 60.0
            
            last_trade_close_time = time.time()
            consecutive_losses = 0
            db_set_state("last_trade_close_time", last_trade_close_time)
            db_set_state("consecutive_losses", consecutive_losses)
            
            msg = (
                f"🚀 *إغلاق ناجح نهائي (TP3) للزوج `${symbol}`!*\n"
                f"💎 *الصافي (بعد الرسوم والـ Slippage):* `${total_pnl:+.2f}` (عائد ROI: `{roi:+.2f}%`)\n"
                f"⏱️ *مدة الصفقة:* `{duration_mins:.1f} دقيقة`"
            )
            send_telegram_alert(msg)
            
            add_db_history({
                "symbol": symbol, "entry_time": trade["entry_time"], "exit_time": time.time(),
                "fees": trade["accumulated_fees"], "roi": roi, "duration_mins": duration_mins,
                "reason_exit": "TP3 Target", "pnl_usdt": total_pnl, "win": True
            })
            remove_db_trade(symbol)
            
        elif current_price >= trade["tp2"] and not trade["tp2_hit"]:
            trade["tp2_hit"] = True
            update_db_trade(trade)
            
            target_sell = trade["initial_quantity"] * 0.33
            success, sold_qty, return_val, avg_exit, _ = execute_spot_sell(symbol, target_sell)
            if success:
                sell_fee = return_val * TRADING_FEE_RATE
                trade["accumulated_fees"] += sell_fee
                trade["realized_usdt"] += return_val
                trade["quantity"] = max(0.0, trade["quantity"] - sold_qty)
                update_db_trade(trade)
                send_telegram_alert(f"🎯 *تم تحقيق الهدف الثاني (TP2) وبيع 33% للزوج `${symbol}`!*")
                
        elif current_price >= trade["tp1"] and not trade["tp1_hit"]:
            trade["tp1_hit"] = True
            trade["sl"] = trade["real_entry_price"]
            update_db_trade(trade)
            
            target_sell = trade["initial_quantity"] * 0.33
            success, sold_qty, return_val, avg_exit, _ = execute_spot_sell(symbol, target_sell)
            if success:
                sell_fee = return_val * TRADING_FEE_RATE
                trade["accumulated_fees"] += sell_fee
                trade["realized_usdt"] += return_val
                trade["quantity"] = max(0.0, trade["quantity"] - sold_qty)
                update_db_trade(trade)
                send_telegram_alert(f"🎯 *تم تحقيق الهدف الأول (TP1) وبيع 33% + تأمين الدخول للزوج `${symbol}`!*")
                
        elif current_price <= trade["sl"]:
            trade["closing"] = True
            update_db_trade(trade)
            
            success, sold_qty, return_val, avg_exit, _ = execute_spot_sell(symbol, trade["quantity"])
            if not success:
                trade["closing"] = False
                update_db_trade(trade)
                continue
                
            sell_fee = return_val * TRADING_FEE_RATE
            trade["accumulated_fees"] += sell_fee
            trade["realized_usdt"] += return_val
            
            total_pnl = trade["realized_usdt"] - trade["invested_usdt"] - trade["accumulated_fees"]
            roi = (total_pnl / trade["invested_usdt"]) * 100
            duration_mins = (time.time() - trade["entry_time"]) / 60.0
            
            last_trade_close_time = time.time()
            is_win = total_pnl > 0
            if is_win:
                consecutive_losses = 0
            else:
                consecutive_losses += 1
                blacklist_symbols[symbol] = time.time() + 86400
                db_set_state("blacklist_symbols", blacklist_symbols)
                
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                    loss_lockout_until = time.time() + LOSS_LOCKOUT_SECONDS
                    db_set_state("loss_lockout_until", loss_lockout_until)
                    send_telegram_alert(f"🚨 *حماية المحفظة:* بلغ عدد الخسائر المتتالية `{consecutive_losses}`. قفل مؤقت لساعة.")
                    consecutive_losses = 0
                    
            db_set_state("last_trade_close_time", last_trade_close_time)
            db_set_state("consecutive_losses", consecutive_losses)
            
            emoji = "🟢" if is_win else "🔴"
            msg = (
                f"{emoji} *إغلاق صفقة الزوج `${symbol}` عبر وقف الخسارة / Trailing.*\n"
                f"📉 *صافي النتيجة:* `${total_pnl:+.2f}` (عائد ROI: `{roi:+.2f}%`)\n"
                f"⏱️ *مدة الصفقة:* `{duration_mins:.1f} دقيقة`"
            )
            send_telegram_alert(msg)
            
            add_db_history({
                "symbol": symbol, "entry_time": trade["entry_time"], "exit_time": time.time(),
                "fees": trade["accumulated_fees"], "roi": roi, "duration_mins": duration_mins,
                "reason_exit": "Stop Loss / Trailing", "pnl_usdt": total_pnl, "win": is_win
            })
            remove_db_trade(symbol)

def send_advanced_report():
    with db_lock, sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trade_history")
        rows = cursor.fetchall()
        
    if not rows:
        return
        
    wins = [r for r in rows if r[9] == 1]
    losses = [r for r in rows if r[9] == 0]
    total_trades = len(rows)
    win_rate = (len(wins) / total_trades) * 100
    
    total_profit = sum(r[8] for r in wins) if wins else 0.0
    total_loss = abs(sum(r[8] for r in losses)) if losses else 0.0
    profit_factor = (total_profit / total_loss) if total_loss > 0 else total_profit
    
    durations = [r[6] for r in rows]
    avg_duration = sum(durations) / len(durations) if durations else 0.0
    max_win = max([r[8] for r in rows], default=0.0)
    max_loss = min([r[8] for r in rows], default=0.0)
    total_fees = sum(r[4] for r in rows)
    
    current_bal = get_account_balance_usdt()
    
    report = (
        f"📊 *التقرير الاحترافي المتقدم لأداء البوت (Institutional Grade v2)*\n"
        f"-----------------------------------\n"
        f"💰 *الرصيد الحر الحالي:* `${current_bal:,.2f} USDT`\n"
        f"📈 *إجمالي الصفقات المنفذة:* `{total_trades}`\n"
        f"🎯 *نسبة النجاح (Win Rate):* `{win_rate:.1f}%`\n"
        f"✅ *الربحة:* `{len(wins)}` | ❌ *الخاسرة:* `{len(losses)}`\n"
        f"💎 *عامل الربح (Profit Factor):* `{profit_factor:.2f}`\n"
        f"⏱️ *متوسط مدة الصفقة:* `{avg_duration:.1f} دقيقة`\n"
        f"🏆 *أكبر ربح:* `${max_win:+.2f}` | 🔻 *أكبر خسارة:* `${max_loss:+.2f}`\n"
        f"💸 *إجمالي الرسوم المدفوعة:* `${total_fees:.2f} USDT`\n"
        f"-----------------------------------\n"
        f"⚡ *تم تفعيل WebSocket للأسعار الحية، Order Book Imbalance، VWAP، و ATR Trailing الحقيقي.*"
    )
    send_telegram_alert(report)

if __name__ == "__main__":
    server_thread = Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    ws_thread = Thread(target=start_websocket)
    ws_thread.daemon = True
    ws_thread.start()
    
    logging.info("تم إطلاق النسخة الاحترافية المطورة بالكامل (Institutional Grade v2).")
    send_telegram_alert("🚀 *تم إطلاق النسخة الاحترافية (v2) لبوت تداول MEXC Spot بكامل التحسينات بنجاح!*")
    
    last_report_time = time.time()
    
    while True:
        try:
            check_and_execute_trades()
            update_active_trades()
            
            if time.time() - last_report_time >= 3600:
                send_advanced_report()
                last_report_time = time.time()
                
        except Exception as e:
            logging.error(f"خطأ رئيسي في حلقة التشغيل: {e}")
        time.sleep(5)
