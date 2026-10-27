from flask import Flask, request, jsonify
import threading
import os
import ccxt
import time

app = Flask(__name__)

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═════════════════════════════════════════════════════════════════════════════
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')

POSITION_SIZE_PERCENT = 1.0  # 1% del balance
LEVERAGE = 5  # 5x apalancamiento

RISK_CONFIG = {
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

# ═════════════════════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES
# ═════════════════════════════════════════════════════════════════════════════
def get_exchange():
    exchange = ccxt.bitunix({
        'apiKey': BITUNIX_API_KEY,
        'secret': BITUNIX_SECRET_KEY,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    exchange.load_markets()
    return exchange

def convert_symbol(symbol):
    """Convierte ZECUSDT.P a ZEC/USDT:USDT para CCXT"""
    if '.P' in symbol and 'USDT' in symbol:
        base = symbol.split('USDT')[0]
        return f"{base}/USDT:USDT"
    return symbol

def get_available_balance(exchange):
    try:
        balance = exchange.fetch_balance()
        if 'USDT' in balance:
            return balance['USDT'].get('free', 0)
        return 0
    except Exception as e:
        print(f"❌ Error balance: {e}")
        return 0

def calculate_atr(exchange, symbol, timeframe='15m', period=14):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=period + 1)
        if len(ohlcv) < period + 1: return None
        true_ranges = []
        for i in range(1, len(ohlcv)):
            high, low, prev_close = ohlcv[i][2], ohlcv[i][3], ohlcv[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        return sum(true_ranges[-period:]) / period
    except Exception as e:
        print(f"❌ Error ATR: {e}")
        return None

def get_risk_params(symbol, entry_price, atr):
    base_symbol = symbol.split('USDT')[0].replace('.', '').replace('/', '')
    risk = RISK_CONFIG.get(base_symbol, RISK_CONFIG['default'])
    sl_mult, tp_rr = risk['sl_atr_mult'], risk['tp_rr']
    
    if atr:
        sl_distance = atr * sl_mult
        return {
            'LONG': {'sl': round(entry_price - sl_distance, 4), 'tp': round(entry_price + (sl_distance * tp_rr), 4)},
            'SHORT': {'sl': round(entry_price + sl_distance, 4), 'tp': round(entry_price - (sl_distance * tp_rr), 4)}
        }
    return {
        'LONG': {'sl': round(entry_price * 0.98, 4), 'tp': round(entry_price * 1.04, 4)},
        'SHORT': {'sl': round(entry_price * 1.02, 4), 'tp': round(entry_price * 0.96, 4)}
    }

def calculate_quantity(exchange, symbol, entry_price, balance, percent, leverage):
    try:
        usdt_to_use = balance * (percent / 100)
        buying_power = usdt_to_use * leverage
        quantity = buying_power / entry_price
        
        market = exchange.market(symbol)
        precision = market['precision']['amount']
        # Ajuste de precisión
        if isinstance(precision, int):
            quantity = round(quantity, precision)
        else:
            # Si es string como '0.001'
            try:
                precision = len(str(precision).split('.')[-1])
                quantity = round(quantity, precision)
            except:
                quantity = round(quantity, 3)
                
        print(f"💰 Cantidad: {quantity} (Balance: {balance}, %: {percent}%, Lev: {leverage}x)")
        return quantity
    except Exception as e:
        print(f"️ Error cantidad: {e}")
        return 0.001

# ═════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ═════════════════════════════════════════════════════════════════════════════
def execute_order(action, raw_symbol, entry_price):
    exchange = None
    try:
        # Convertir símbolo (ZECUSDT.P -> ZEC/USDT:USDT)
        symbol = convert_symbol(raw_symbol)
        print(f"🔄 Símbolo convertido: {raw_symbol} -> {symbol}")
        
        exchange = get_exchange()
        balance = get_available_balance(exchange)
        
        if balance <= 0:
            print("❌ Sin balance")
            return
            
        exchange.set_leverage(LEVERAGE, symbol)
        atr = calculate_atr(exchange, symbol)
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        
        print(f"📊 SL: {params['sl']} | TP: {params['tp']}")
        
        quantity = calculate_quantity(exchange, symbol, float(entry_price), balance, POSITION_SIZE_PERCENT, LEVERAGE)
        
        print(f"📤 Ejecutando {action}...")
        if action == 'LONG':
            order = exchange.create_market_buy_order(symbol, quantity)
        else:
            order = exchange.create_market_sell_order(symbol, quantity)
            
        print(f"✅ Orden ejecutada: {order.get('id')}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if exchange: exchange.close()

def process_order(action, symbol, price):
    if not BITUNIX_API_KEY:
        print("❌ Faltan API Keys")
        return
    execute_order(action, symbol, float(price))

# ═════════════════════════════════════════════════════════════════════════════
# WEBHOOKS
# ═════════════════════════════════════════════════════════════════════════════
@app.route('/', methods=['GET'])
def index(): return 'Bot running!'

@app.route('/health', methods=['GET'])
def health(): return jsonify({'status': 'ok'}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.get_data(as_text=True)
        print(f"📨 {message}")
        parts = message.split('|')
        if len(parts) >= 3:
            action, symbol, price = parts[0].strip().upper(), parts[1].strip(), parts[2].strip()
            if action in ['LONG', 'SHORT']:
                threading.Thread(target=process_order, args=(action, symbol, price)).start()
                return jsonify({'status': 'success'}), 200
        return jsonify({'status': 'error'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
