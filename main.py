from flask import Flask
import requests
import time
import os
import json
from threading import Thread
from datetime import datetime, timezone

# 1️⃣ خادم Flask لإبقاء الخدمة شغالة 24/7 على Render
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Solana Fast Sniper Meme Bot is Running 24/7!", 200

def start_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# 2️⃣ إعدادات التليجرام (بوت الميم كوينز)
TELEGRAM_BOT_TOKEN = "8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0"
CHAT_ID = "1015963752"

# 3️⃣ إدارة المحفظة والأداء وحفظ البيانات
USD_TO_SAR = 3.75
INITIAL_BALANCE_SAR = 1000.0
INITIAL_BALANCE_USD = INITIAL_BALANCE_SAR / USD_TO_SAR

STATE_FILE = "bot_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                saved_symbols = data.get("traded_symbols", [])
                if len(saved_symbols) > 1000:
                    saved_symbols = saved_symbols[-1000:]
                return (
                    data.get("balance_usd", INITIAL_BALANCE_USD),
                    data.get("active_trades", {}),
                    data.get("trade_history", []),
                    set(saved_symbols)
                )
        except Exception as e:
            print(f"⚠️ خطأ في قراءة ملف الحفظ: {e}")
    return INITIAL_BALANCE_USD, {}, [], set()

def save_state():
    try:
        global traded_symbols
        if len(traded_symbols) > 1000:
            traded_symbols = set(list(traded_symbols)[-1000:])
            
        data = {
            "balance_usd": balance_usd,
            "active_trades": active_trades,
            "trade_history": trade_history,
            "traded_symbols": list(traded_symbols)
        }
        
        temp_file = "bot_state.tmp"
        with open(temp_file, "w") as f:
            json.dump(data, f)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        print(f"⚠️ خطأ في حفظ الملف: {e}")

balance_usd, active_trades, trade_history, traded_symbols = load_state()
MAX_CONCURRENT_TRADES = 3

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ خطأ تليجرام: {e}")

def scan_solana_meme_coins():
    # جلب أسرع للعملات الجديدة والنشطة عبر endpoints متعددة ومباشرة لتقليل أي تأخير
    meme_opportunities = []
    seen_addresses = set()
    
    # قائمة مصادر سريعة متعددة لتغطية أحدث الأزواج في سولانا
    urls = [
        "https://api.dexscreener.com/latest/dex/tokens/solana",
        "https://api.dexscreener.com/latest/dex/search?q=solana"
    ]
    
    for url in urls:
        try:
            res = requests.get(url, timeout=4)
            if res.status_code == 200:
                data = res.json()
                pairs = data.get("pairs", []) if isinstance(data, dict) else data
                
                for pair in pairs:
                    if pair.get("chainId") != "solana":
                        continue
                    
                    token_address = str(pair.get("address", ""))
                    if not token_address or token_address in seen_addresses:
                        continue
                    seen_addresses.add(token_address)
                    
                    token_info = pair.get("baseToken", {})
                    symbol = str(token_info.get("symbol", "UNKNOWN")).upper()
                    name = str(token_info.get("name", "")).upper()
                    
                    excluded_tokens = ["SOL", "WSOL", "USDC", "USDT", "WBTC", "ETH"]
                    if symbol in excluded_tokens or "SOLANA" in name:
                        continue
                    
                    market_cap = float(pair.get("marketCap", pair.get("fdv", 0)) or 0)
                    price = float(pair.get("priceUsd", 0) or 0)
                    
                    if price <= 0:
                        continue
                    
                    if not (5000 <= market_cap <= 120000):
                        continue
                    
                    pair_created_at_ms = pair.get("pairCreatedAt", 0)
                    if not pair_created_at_ms:
                        continue
                    
                    creation_time = datetime.fromtimestamp(pair_created_at_ms / 1000.0, tz=timezone.utc)
                    age_minutes = (datetime.now(timezone.utc).timestamp() - (pair_created_at_ms / 1000.0)) / 60.0
                    creation_time_str = creation_time.strftime("%Y-%m-%d %H:%M:%S")
                    
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
                    
                    txns = pair.get("txns", {})
                    h24_txns = txns.get("h24", {})
                    buys = int(h24_txns.get("buys", 0) or 0)
                    sells = int(h24_txns.get("sells", 0) or 0)
                    total_txns = buys + sells
                    
                    dex_url = str(pair.get("url", f"https://dexscreener.com/solana/{token_address}"))
                    
                    # نظام النقاط (Score System)
                    score = 0
                    if liquidity >= 10000:
                        score += 30
                    if volume_24h >= 20000:
                        score += 20
                    if age_minutes < 60:
                        score += 20
                    if market_cap < 50000:
                        score += 20
                    if buys > sells:
                        score += 10
                    
                    if score >= 80:
                        meme_opportunities.append({
                            "symbol": symbol,
                            "name": name,
                            "address": token_address,
                            "price": price,
                            "market_cap": market_cap,
                            "liquidity_usd": liquidity,
                            "volume_h24": volume_24h,
                            "buys": buys,
                            "sells": sells,
                            "txns_h24": total_txns,
                            "age_minutes": age_minutes,
                            "creation_time": creation_time_str,
                            "score": score,
                            "url": dex_url
                        })
        except Exception as e:
            print(f"⚠️ خطأ في الاتصال بالمصدر السريع: {e}")
            
    return meme_opportunities

def check_and_execute_meme_trades():
    global balance_usd
    
    if len(active_trades) >= MAX_CONCURRENT_TRADES:
        return
        
    opportunities = scan_solana_meme_coins()
    
    for opp in opportunities:
        symbol = opp["symbol"]
        name = opp["name"]
        address = opp["address"]
        price = opp["price"]
        market_cap = opp["market_cap"]
        liq_usd = opp["liquidity_usd"]
        vol_h24 = opp["volume_h24"]
        buys = opp["buys"]
        sells = opp["sells"]
        txns_h24 = opp["txns_h24"]
        age_minutes = opp["age_minutes"]
        creation_time = opp["creation_time"]
        score = opp["score"]
        dex_url = opp["url"]
        
        if price <= 0 or symbol in active_trades or symbol in traded_symbols:
            continue
            
        trade_amount_usd = balance_usd * 0.20
        if trade_amount_usd < 5:
            continue
            
        balance_usd -= trade_amount_usd
        tokens = trade_amount_usd / price
        
        tp_price = price * 1.08
        sl_price = price * 0.97
        
        active_trades[symbol] = {
            "pair": address,
            "entry_price": price,
            "tokens": tokens,
            "invested_usd": trade_amount_usd,
            "tp": tp_price,
            "sl": sl_price
        }
        
        traded_symbols.add(symbol)
        save_state()
        
        msg = (
            f"🚀 *قنص ميم كوين سريع (سرعة فائقة)!*\n"
            f"-----------------------------------\n"
            f"🪙 *العملة:* `{symbol}` ({name})\n"
            f"⭐ *النقاط (Score):* `{score}/100`\n"
            f"⏱️ *وقت الإنشاء:* `{creation_time}`\n"
            f"⏳ *عمر العملة:* `{age_minutes:.1f} دقيقة`\n"
            f"📊 *القيمة السوقية:* `${market_cap:,.0f}`\n"
            f"💧 *السيولة:* `${liq_usd:,.0f}`\n"
            f"📈 *حجم التداول:* `${vol_h24:,.0f}`\n"
            f"🛒 *المشترين:* `{buys:,}` | 🛍️ *البائعين:* `{sells:,}`\n"
            f"💵 *سعر القنص:* `${price:,.8f}`\n"
            f"💰 *المبلغ المستثمر:* `${trade_amount_usd:.2f}` ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n"
            f"🎯 *الهدف (TP):* `${tp_price:,.8f}` (+8.0%)\n"
            f"🛑 *وقف الخسارة (SL):* `${sl_price:,.8f}` (-3.0%)\n"
            f"🔗 *عقد الزوج:* `{address}`\n"
            f"📈 [عرض الشارت السريع]({dex_url})\n"
            f"💼 *الرصيد المتبقي:* `${balance_usd:.2f}`"
        )
        send_telegram_alert(msg)
        
        if len(active_trades) >= MAX_CONCURRENT_TRADES:
            break

def update_meme_trades():
    global balance_usd
    
    for symbol, trade in list(active_trades.items()):
        try:
            token_address = trade.get("pair")
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            res = requests.get(url, timeout=3)
            
            current_price = 0.0
            if res.status_code == 200:
                data = res.json()
                pairs = data.get("pairs", [])
                if pairs:
                    current_price = float(pairs[0].get("priceUsd", 0) or 0)
            
            if current_price <= 0:
                continue
                
            if current_price >= trade["tp"]:
                return_usd = trade["tokens"] * current_price
                pnl_usd = return_usd - trade["invested_usd"]
                balance_usd += return_usd
                
                msg = (
                    f"🚀 *تم تحقيق الهدف بنجاح! (+8%)*\n"
                    f"🪙 *العملة:* `${symbol}`\n"
                    f"💰 *الربح:* `+${pnl_usd:.2f}` (`+{pnl_usd * USD_TO_SAR:.1f}` ريال)\n"
                    f"💼 *الرصيد الجديد:* `${balance_usd:.2f}`"
                )
                send_telegram_alert(msg)
                trade_history.append({"symbol": symbol, "pnl_usd": pnl_usd, "win": True})
                del active_trades[symbol]
                save_state()
                
            elif current_price <= trade["sl"]:
                return_usd = trade["tokens"] * current_price
                pnl_usd = return_usd - trade["invested_usd"]
                balance_usd += return_usd
                
                msg = (
                    f"🛑 *ضرب وقف الخسارة للميم! (-3%)*\n"
                    f"🪙 *العملة:* `${symbol}`\n"
                    f"📉 *الخسارة:* `${pnl_usd:.2f}` (`{pnl_usd * USD_TO_SAR:.1f}` ريال)\n"
                    f"💼 *الرصيد الجديد:* `${balance_usd:.2f}`"
                )
                send_telegram_alert(msg)
                trade_history.append({"symbol": symbol, "pnl_usd": pnl_usd, "win": False})
                del active_trades[symbol]
                save_state()
        except Exception as e:
            print(f"❌ خطأ في التحديث السريع: {e}")

def send_hourly_report():
    unrealized_usd = 0.0
    for symbol, trade in active_trades.items():
        try:
            token_address = trade.get("pair")
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            res = requests.get(url, timeout=3)
            price = 0.0
            if res.status_code == 200:
                pairs = res.json().get("pairs", [])
                if pairs:
                    price = float(pairs[0].get("priceUsd", 0) or 0)
            
            if price > 0:
                unrealized_usd += trade["tokens"] * price
            else:
                unrealized_usd += trade["invested_usd"]
        except:
            unrealized_usd += trade["invested_usd"]
            
    total_equity_usd = balance_usd + unrealized_usd
    total_equity_sar = total_equity_usd * USD_TO_SAR
    
    pnl_sar = total_equity_sar - INITIAL_BALANCE_SAR
    pnl_pct = (pnl_sar / INITIAL_BALANCE_SAR) * 100
    
    wins = sum(1 for t in trade_history if t["win"])
    total_closed = len(trade_history)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    
    report = (
        f"📊 *التقرير الساعي لبوت قنص الميم كوين*\n"
        f"-----------------------------------\n"
        f"💰 *رأس المال الحالي:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n"
        f"📈 *صافي الأرباح/الخسائر:* {pnl_sar:+.2f} ريال ({pnl_pct:+.2f}%)\n"
        f"🔄 *الصفقات المفتوحة:* {len(active_trades)}/{MAX_CONCURRENT_TRADES}\n"
        f"✅ *الصفقات المغلقة:* {total_closed} (ناجحة: {wins} | خاسرة: {total_closed - wins})\n"
        f"🎯 *نسبة النجاح:* {win_rate:.1f}%\n"
        f"-----------------------------------\n"
        f"⚡ *وضع الأداء الفائق والسرعة العالية مفعل 24/7*"
    )
    send_telegram_alert(report)

if __name__ == "__main__":
    server_thread = Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    welcome_msg = (
        "🚀 *تم تشغيل بوت قنص الميم كوين (بأقصى سرعة استجابة) بنجاح!*\n"
        "⚡ *المميزات:* مسح متوازي سريع للبيانات، نظام النقاط الشامل، الحفظ الآمن."
    )
    send_telegram_alert(welcome_msg)
    
    last_report_time = time.time()
    
    while True:
        try:
            check_and_execute_meme_trades()
            update_meme_trades()
            
            if time.time() - last_report_time >= 3600:
                send_hourly_report()
                last_report_time = time.time()
                
        except Exception as e:
            print(f"⚠️ خطأ رئيسي: {e}")
            
        time.sleep(3)
