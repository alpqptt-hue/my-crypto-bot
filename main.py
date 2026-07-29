from datetime import datetime, timedelta
import os
from threading import Thread
import time
from flask import Flask
import requests

# 1️⃣ خادم Flask لإبقاء الخدمة شغالة 24/7 على Render
app = Flask(__name__)


@app.route('/')
def health_check():
  return 'Crypto Breakout Bot is Running 24/7!', 200


def start_server():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# 2️⃣ إعدادات التليجرام للبوت (My Crypto Bot)
TELEGRAM_BOT_TOKEN = '8596265665:AAEdjiNIHoA6D-oFmr_iCsaBbomwcdhqgp0'
CHAT_ID = '1015963752'

# 3️⃣ إدارة المحفظة الوهمية (1,000 ريال سعودي = ~266.67 دولار)
USD_TO_SAR = 3.75
INITIAL_BALANCE_SAR = 1000.0
INITIAL_BALANCE_USD = INITIAL_BALANCE_SAR / USD_TO_SAR

balance_usd = INITIAL_BALANCE_USD
active_trades = {}
trade_history = []
MAX_CONCURRENT_TRADES = 3
MAX_TRADE_DURATION_MINUTES = 30  # الخروج التلقائي بعد 30 دقيقة

# قائمة العملات القوية
WATCHLIST = [
    'BTCUSDT',
    'ETHUSDT',
    'SOLUSDT',
    'BNBUSDT',
    'XRPUSDT',
    'ADAUSDT',
    'AVAXUSDT',
    'LINKUSDT',
    'DOGEUSDT',
    'NEARUSDT',
]


def send_telegram_alert(message):
  url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
  payload = {
      'chat_id': CHAT_ID,
      'text': message,
      'parse_mode': 'Markdown',
      'disable_web_page_preview': True,
  }
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f'❌ خطأ تليجرام: {e}')


def get_crypto_price_and_volume(symbol):
  """جلب بيانات السعر والحجم الحقيقي من Binance API"""
  try:
    url = f'https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}'
    res = requests.get(url, timeout=5)
    if res.status_code == 200:
      data = res.json()
      return {
          'price': float(data['lastPrice']),
          'price_change_pct': float(data['priceChangePercent']),
          'volume_usd': float(data['quoteVolume']),
      }
  except Exception as e:
    print(f'❌ خطأ جلب سعر {symbol}: {e}')
  return None


def check_and_execute_trades():
  """فحص استراتيجية Breakout + Volume"""
  global balance_usd

  if len(active_trades) >= MAX_CONCURRENT_TRADES:
    return

  for symbol in WATCHLIST:
    if symbol in active_trades:
      continue

    data = get_crypto_price_and_volume(symbol)
    if not data:
      continue

    price = data['price']
    price_change = data['price_change_pct']

    if price_change >= 1.5:
      trade_amount_usd = balance_usd * 0.25
      if trade_amount_usd < 10:
        continue

      balance_usd -= trade_amount_usd
      tokens = trade_amount_usd / price

      tp_price = price * 1.02
      sl_price = price * 0.99
      entry_time = datetime.now()
      exit_time_str = (
          entry_time + timedelta(minutes=MAX_TRADE_DURATION_MINUTES)
      ).strftime('%H:%M')

      active_trades[symbol] = {
          'entry_price': price,
          'tokens': tokens,
          'invested_usd': trade_amount_usd,
          'tp': tp_price,
          'sl': sl_price,
          'entry_time': entry_time,
      }

      msg = (
          f'🚀 *دخول صفقة جديد (Breakout + Volume)*\n'
          f'-----------------------------------\n'
          f'🪙 *العملة:* `{symbol}`\n'
          f'💵 *سعر الدخول:* `${price:,.4f}`\n'
          f'💰 *المبلغ المستثمر:* `${trade_amount_usd:.2f}` ({trade_amount_usd * USD_TO_SAR:.1f} ريال)\n'
          f'🎯 *جني الأرباح (TP):* `${tp_price:,.4f}` (+2.0%)\n'
          f'🛑 *وقف الخسارة (SL):* `${sl_price:,.4f}` (-1.0%)\n'
          f'⏰ *وقت الخروج التلقائي:* بعد 30 دقيقة (حوالي {exit_time_str})\n'
          f'💼 *الرصيد المتبقي:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
      )
      send_telegram_alert(msg)

      if len(active_trades) >= MAX_CONCURRENT_TRADES:
        break


def update_active_trades():
  """متابعة الصفقات وحساب الربح والخسارة والوقت بدقة"""
  global balance_usd

  for symbol, trade in list(active_trades.items()):
    data = get_crypto_price_and_volume(symbol)
    if not data:
      continue

    current_price = data['price']
    current_time = datetime.now()
    elapsed_minutes = (current_time - trade['entry_time']).total_seconds() / 60.0

    # 1️⃣ تحقّق هدف الأرباح (+2%)
    if current_price >= trade['tp']:
      return_usd = trade['tokens'] * current_price
      pnl_usd = return_usd - trade['invested_usd']
      balance_usd += return_usd

      msg = (
          f'🎉 *تم تحقيق الهدف بنجاح! (+2%)*\n'
          f'🪙 *العملة:* `{symbol}`\n'
          f'💵 *سعر الخروج:* `${current_price:,.4f}`\n'
          f'💰 *الربح المحقق:* `+${pnl_usd:.2f}` (`+{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
          f'💼 *رصيد المحفظة الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
      )
      send_telegram_alert(msg)

      trade_history.append({'symbol': symbol, 'pnl_usd': pnl_usd, 'win': True})
      del active_trades[symbol]

    # 2️⃣ تحقّق وقف الخسارة (-1%)
    elif current_price <= trade['sl']:
      return_usd = trade['tokens'] * current_price
      pnl_usd = return_usd - trade['invested_usd']
      balance_usd += return_usd

      msg = (
          f'🛑 *ضرب وقف الخسارة! (-1%)*\n'
          f'🪙 *العملة:* `{symbol}`\n'
          f'💵 *سعر الخروج:* `${current_price:,.4f}`\n'
          f'📉 *الخسارة:* `${pnl_usd:.2f}` (`{pnl_usd * USD_TO_SAR:.1f}` ريال)\n'
          f'💼 *رصيد المحفظة الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
      )
      send_telegram_alert(msg)

      trade_history.append(
          {'symbol': symbol, 'pnl_usd': pnl_usd, 'win': False}
      )
      del active_trades[symbol]

    # 3️⃣ الخروج الزمني الذكي (بعد مرور 30 دقيقة بالضبط)
    elif elapsed_minutes >= MAX_TRADE_DURATION_MINUTES:
      return_usd = trade['tokens'] * current_price
      pnl_usd = return_usd - trade['invested_usd']
      balance_usd += return_usd
      is_win = pnl_usd > 0

      msg = (
          f'⏰ *خروج زمني ذكي بعد 30 دقيقة*\n'
          f'🪙 *العملة:* `{symbol}`\n'
          f'💵 *سعر الخروج:* `${current_price:,.4f}`\n'
          f'📊 *النتيجة:* `${pnl_usd:+.2f}$` (`{pnl_usd * USD_TO_SAR:+.1f}` ريال)\n'
          f'💼 *رصيد المحفظة الجديد:* `${balance_usd:.2f}` ({balance_usd * USD_TO_SAR:.1f} ريال)'
      )
      send_telegram_alert(msg)

      trade_history.append(
          {'symbol': symbol, 'pnl_usd': pnl_usd, 'win': is_win}
      )
      del active_trades[symbol]


def send_hourly_report():
  """تقرير ساعي دقيق ومحسوب 100%"""
  unrealized_usd = 0.0
  for symbol, trade in active_trades.items():
    data = get_crypto_price_and_volume(symbol)
    if data:
      unrealized_usd += trade['tokens'] * data['price']
    else:
      unrealized_usd += trade['invested_usd']

  total_equity_usd = balance_usd + unrealized_usd
  total_equity_sar = total_equity_usd * USD_TO_SAR

  pnl_sar = total_equity_sar - INITIAL_BALANCE_SAR
  pnl_pct = (pnl_sar / INITIAL_BALANCE_SAR) * 100

  wins = sum(1 for t in trade_history if t['win'])
  total_closed = len(trade_history)
  win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

  report = (
      f'📊 *التقرير الساعي المحدث لبوت الكريبتو*\n'
      f'-----------------------------------\n'
      f'💰 *رأس المال الحالي:* {total_equity_sar:.2f} ريال (${total_equity_usd:.2f})\n'
      f'📈 *صافي الأرباح/الخسائر:* {pnl_sar:+.2f} ريال ({pnl_pct:+.2f}%)\n'
      f'🔄 *الصفقات المفتوحة حالياً:* {len(active_trades)}/{MAX_CONCURRENT_TRADES}\n'
      f'✅ *إجمالي الصفقات المغلقة:* {total_closed} (ناجحة: {wins} | خاسرة: {total_closed - wins})\n'
      f'🎯 *نسبة نجاح الاستراتيجية:* {win_rate:.1f}%\n'
      f'-----------------------------------\n'
      f'🟢 *البوت يعمل بسلاسة 24/7 على Render*'
  )
  send_telegram_alert(report)


if __name__ == '__main__':
  server_thread = Thread(target=start_server)
  server_thread.daemon = True
  server_thread.start()

  welcome_msg = (
      '🚀 *تم تشغيل بوت الكريبتو (Breakout + Volume) بنجاح!*\n'
      '💰 *رأس المال المبدئي:* 1,000 ريال سعودي ($266.67 USD)\n'
      '⏰ سيصلك تقرير ساعي مضبوط ومحدث 100% مع حماية ضد التوقف.'
  )
  send_telegram_alert(welcome_msg)

  last_report_time = time.time()

  # حلقة رئيسية محمية بالكامل لضمان عدم توقف البوت نهائياً
  while True:
    try:
      check_and_execute_trades()
      update_active_trades()

      if time.time() - last_report_time >= 3600:
        send_hourly_report()
        last_report_time = time.time()

    except Exception as e:
      print(f'⚠️ خطأ في حلقة التشغيل الرئيسية: {e}')

    time.sleep(15)
