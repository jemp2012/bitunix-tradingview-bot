from flask import Flask, request, jsonify
import threading
import os
import time
import hashlib
import requests
import secrets
import json

app = Flask(__name__)

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═════════════════════════════════════════════════════════════════════════════
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')

POSITION_SIZE_PERCENT = 1.0  # 1% del balance
LEVERAGE = 5                 # 5x apalancamiento

BITUNIX_BASE_URL = 'https://fapi.bitunix.com'

RISK_CONFIG = {
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

TRAILING_CONFIG = {
    'activation_pct': 1.0,   # Activar BE/Trailing al 1% de ganancia
    'trail_pct': 0.5,        # Distancia del trailing stop (0.5%)
    'check_interval': 10     # Verificar cada 10 segundos
}

# ════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN BITUNIX (SHA256 DOBLE)
# ═════════════════════════════════════════════════════════════════════════════
def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def generate_signature(secret_key, timestamp, nonce, api_key, query_string='', body=''):
    message = f"{nonce}{timestamp}{api_key}{query_string}{body}"
    digest = sha256_hex(message)
    sign = sha256_hex(f"{digest}{secret_key}")
    return sign

def bitunix_request(method, endpoint, params=None, data=None, auth=False):
    url = f"{BITUNIX_BASE_URL}{endpoint}"
    timestamp = time.strftime('%Y%m%d%H%M%S', time.gmtime())
    nonce = secrets.token_hex(8)
    
    headers = {'Content-Type': 'application/json', 'language': 'en-US'}
    query_string = ''
    body_str = ''
    
    if auth:
        headers['api-key'] = BITUNIX_API_KEY
        headers['timestamp'] = timestamp
        headers['nonce'] = nonce
        
        if params:
            sorted_params = sorted(params.items())
            query_string = ''.join([f"{k}{v}" for k, v in sorted_params])
        
        if data:
            body_str = json.dumps(data, separators=(',', ':'), sort_keys=True)
        
        signature = generate_signature(BITUNIX_SECRET_KEY, timestamp, nonce, BITUNIX_API_KEY, query_string, body_str)
        headers['sign'] = signature
    
    try:
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=10)
        elif method == 'POST':
            response = requests.post(url, data=body_str, headers=headers, timeout=10)
        
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Error en API request: {e}")
        return None

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE BITUNIX
# ═════════════════════════════════════════════════════════════════════════════
def get_balance():
    result = bitunix_request('GET', '/api/v1/futures/account', auth=True)
    if result and result.get('code') == 0:
        data = result.get('data', {})
        if isinstance(data, list):
            for item in data:
                if item.get('marginCoin') == 'USDT':
                    return float(item.get('available', 0))
        elif isinstance(data, dict):
            return float(data.get('available', 0))
    return 0

def get_klines(symbol, interval='15m', limit=20):
    result = bitunix_request('GET', '/api/v1/futures/market/kline', params={
        'symbol': symbol, 'interval': interval, 'limit': limit
    })
    if result and result.get('code') == 0:
        return result.get('data', [])
    return []

def calculate_atr_from_klines(klines, period=14):
    if len(klines) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(klines)):
        if isinstance(klines[i], dict):
            high = float(klines[i].get('high', 0))
            low = float(klines[i].get('low', 0))
            prev_close = float(klines[i-1].get('close', 0))
        else:
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges[-period:]) / period

def set_leverage(symbol, leverage):
    result = bitunix_request('POST', '/api/v1/futures/account/change_leverage', data={
        'symbol': symbol, 'leverage': leverage, 'marginCoin': 'USDT'
    }, auth=True)
    return result and result.get('code') == 0

def place_order(symbol, side, qty):
    result = bitunix_request('POST', '/api/v1/futures/trade/place_order', data={
        'symbol': symbol, 'qty': str(qty), 'side': side,
        'tradeSide': 'OPEN', 'orderType': 'MARKET'
    }, auth=True)
    if result and result.get('code') == 0:
        return result.get('data')
    return None

def get_position_id(symbol):
    """Obtiene el positionId de la posición más reciente"""
    result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
    if result and result.get('code') == 0:
        data = result.get('data', [])
        if isinstance(data, list):
            for pos in data:
                if pos.get('symbol') == symbol:
                    return pos.get('positionId')
    return None

def place_stop_loss(symbol, position_id, qty, sl_price):
    """Coloca Stop Loss usando el endpoint correcto de Bitunix con slQty"""
    print(f"🛡️ Colocando SL en {sl_price} para {symbol} (Position ID: {position_id}, Qty: {qty})...")
    result = bitunix_request('POST', '/api/v1/futures/tpsl/place_order', data={
        'symbol': symbol,
        'positionId': str(position_id),
        'slPrice': str(round(sl_price, 4)),
        'slStopType': 'LAST_PRICE',
        'slOrderType': 'MARKET',
        'slQty': str(qty)  # Parámetro requerido según la documentación
    }, auth=True)
    
    if result and result.get('code') == 0:
        print(f"✅ Stop Loss colocado exitosamente: {sl_price}")
        return result.get('data')
    else:
        print(f"⚠️ Error colocando SL: {result}")
        return None

def get_current_price(symbol):
    result = bitunix_request('GET', '/api/v1/futures/market/ticker', params={'symbol': symbol})
    if result and result.get('code') == 0:
        data = result.get('data', {})
        if isinstance(data, list) and len(data) > 0:
            return float(data[0].get('lastPrice', 0))
        elif isinstance(data, dict):
            return float(data.get('lastPrice', 0))
    return None

# ════════════════════════════════════════════════════════════════════════════
# MONITOREO DE POSICIÓN (BE + TRAILING STOP)
# ═════════════════════════════════════════════════════════════════════════════
def monitor_position(symbol, entry_price, action, qty, initial_sl, position_id):
    print(f"\n👁️ Iniciando monitoreo de posición: {symbol} {action}")
    print(f"   Entry: {entry_price} | SL inicial: {initial_sl}")
    
    trailing_activated = False
    be_activated = False
    highest_price = entry_price
    lowest_price = entry_price
    
    while True:
        try:
            current_price = get_current_price(symbol)
            if not current_price:
                time.sleep(TRAILING_CONFIG['check_interval'])
                continue
            
            if action == 'LONG':
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                if current_price > highest_price:
                    highest_price = current_price
            else:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                if current_price < lowest_price:
                    lowest_price = current_price
            
            if pnl_pct >= TRAILING_CONFIG['activation_pct'] and not be_activated:
                print(f"🎯 ¡BE activado! PnL: {pnl_pct:.2f}%")
                place_stop_loss(symbol, position_id, qty, entry_price)
                be_activated = True
                trailing_activated = True
            
            if trailing_activated:
                if action == 'LONG':
                    new_sl = highest_price * (1 - TRAILING_CONFIG['trail_pct'] / 100)
                    if new_sl > entry_price:
                        place_stop_loss(symbol, position_id, qty, new_sl)
                        print(f"📈 Trailing SL actualizado: {new_sl:.2f} (Highest: {highest_price:.2f})")
                else:
                    new_sl = lowest_price * (1 + TRAILING_CONFIG['trail_pct'] / 100)
                    if new_sl < entry_price:
                        place_stop_loss(symbol, position_id, qty, new_sl)
                        print(f"📉 Trailing SL actualizado: {new_sl:.2f} (Lowest: {lowest_price:.2f})")
            
            time.sleep(TRAILING_CONFIG['check_interval'])
        except Exception as e:
            print(f"⚠️ Error en monitoreo: {e}")
            time.sleep(TRAILING_CONFIG['check_interval'])

# ════════════════════════════════════════════════════════════════════════════
# LÓGICA DE TRADING
# ═════════════════════════════════════════════════════════════════════════════
def convert_symbol(symbol):
    if '.P' in symbol:
        return symbol.replace('.P', '')
    return symbol

def get_risk_params(symbol, entry_price, atr):
    risk = RISK_CONFIG.get(symbol, RISK_CONFIG['default'])
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

def calculate_quantity(balance, entry_price, percent, leverage):
    usdt_to_use = balance * (percent / 100)
    buying_power = usdt_to_use * leverage
    quantity = buying_power / entry_price
    return round(quantity, 3)

def execute_order(action, raw_symbol, entry_price):
    try:
        print(f"\n{'='*60}")
        print(f"🚀 Iniciando ejecución: {action} {raw_symbol} @ {entry_price}")
        print(f"{'='*60}\n")
        
        symbol = convert_symbol(raw_symbol)
        print(f"✅ Símbolo convertido: {raw_symbol} -> {symbol}")
        
        balance = get_balance()
        if balance <= 0:
            print("❌ Balance es 0 o error")
            return
        
        set_leverage(symbol, LEVERAGE)
        
        klines = get_klines(symbol, '15m', 20)
        atr = calculate_atr_from_klines(klines, 14)
        
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        print(f"📊 SL inicial: {params['sl']} | TP: {params['tp']}")
        
        quantity = calculate_quantity(balance, float(entry_price), POSITION_SIZE_PERCENT, LEVERAGE)
        print(f"💰 Cantidad calculada: {quantity}")
        
        print("🚀 EJECUTANDO ORDEN DE MERCADO...")
        side = 'BUY' if action == 'LONG' else 'SELL'
        order_result = place_order(symbol, side, quantity)
        print(f"✅ Orden ejecutada: {order_result}")
        
        if order_result:
            print("⏳ Esperando 2 segundos a que Bitunix registre la posición...")
            time.sleep(2)
            
            position_id = get_position_id(symbol)
            if position_id:
                print(f"✅ Position ID obtenido: {position_id}")
                print(f"🛡️ Colocando Stop Loss inicial...")
                sl_result = place_stop_loss(symbol, position_id, quantity, params['sl'])
                
                if sl_result:
                    print(f"👁️ Iniciando hilo de monitoreo BE/Trailing...")
                    monitor_thread = threading.Thread(
                        target=monitor_position,
                        args=(symbol, float(entry_price), action, quantity, params['sl'], position_id),
                        daemon=True
                    )
                    monitor_thread.start()
            else:
                print(f"⚠️ No se pudo obtener el positionId. El SL no se colocó automáticamente.")
        
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"❌ Error en execute_order: {e}")
        import traceback
        traceback.print_exc()

def process_order(action, symbol, price):
    print(f"\n🔔 Process order llamado: {action} {symbol} {price}")
    if not BITUNIX_API_KEY:
        print("❌ Faltan API Keys")
        return
    execute_order(action, symbol, float(price))

# ════════════════════════════════════════════════════════════════════════════
# WEBHOOKS
# ════════════════════════════════════════════════════════════════════════════
@app.route('/', methods=['GET'])
def index():
    return 'Bot running!'

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.get_data(as_text=True)
        print(f"📨 Webhook recibido: {message}")
        parts = message.split('|')
        if len(parts) >= 3:
            action, symbol, price = parts[0].strip().upper(), parts[1].strip(), parts[2].strip()
            if action in ['LONG', 'SHORT']:
                print(f"✅ Acción válida: {action}")
                threading.Thread(target=process_order, args=(action, symbol, price)).start()
                return jsonify({'status': 'success'}), 200
        return jsonify({'status': 'error'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
