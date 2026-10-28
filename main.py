from flask import Flask, request, jsonify
import threading
import os
import time
import hmac
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
LEVERAGE = 5  # 5x apalancamiento

BITUNIX_BASE_URL = 'https://fapi.bitunix.com'

RISK_CONFIG = {
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

# ════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN BITUNIX
# ═════════════════════════════════════════════════════════════════════════════
def generate_signature(secret_key, timestamp, nonce, method, path, query_string='', body=''):
    """Genera la firma HMAC SHA256 según docs de Bitunix"""
    message = f"{timestamp}{method}{path}{query_string}{body}"
    signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def bitunix_request(method, endpoint, params=None, data=None, auth=False):
    """Hace una petición a la API de Bitunix"""
    url = f"{BITUNIX_BASE_URL}{endpoint}"
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(32)  # 64 caracteres hex
    
    headers = {
        'Content-Type': 'application/json',
        'language': 'en-US'
    }
    
    if auth:
        headers['api-key'] = BITUNIX_API_KEY
        headers['timestamp'] = timestamp
        headers['nonce'] = nonce
        
        query_string = ''
        if params:
            query_string = '?' + '&'.join([f"{k}={v}" for k, v in params.items()])
        
        body_str = ''
        if data:
            body_str = json.dumps(data, separators=(',', ':'))
        
        signature = generate_signature(BITUNIX_SECRET_KEY, timestamp, nonce, method, endpoint, query_string, body_str)
        headers['sign'] = signature
    
    try:
        print(f"🔗 Request: {method} {url}")
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=10)
        elif method == 'POST':
            response = requests.post(url, data=body_str if data else None, headers=headers, timeout=10)
        
        print(f"📡 Response status: {response.status_code}")
        print(f"📡 Response body: {response.text[:500]}")
        
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Error en API request: {e}")
        return None

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE BITUNIX
# ═════════════════════════════════════════════════════════════════════════════
def get_balance():
    print("🔍 Obteniendo balance...")
    result = bitunix_request('GET', '/api/v1/futures/account/get_single_account', auth=True)
    
    if result:
        if result.get('code') == 0:
            data = result.get('data', {})
            if isinstance(data, list):
                for item in data:
                    if item.get('marginCoin') == 'USDT':
                        free = float(item.get('available', 0))
                        print(f"💰 Balance USDT disponible: {free:.2f}")
                        return free
            elif isinstance(data, dict):
                free = float(data.get('available', 0))
                print(f"💰 Balance USDT disponible: {free:.2f}")
                return free
    print(f"⚠️ No se pudo obtener balance. Respuesta: {result}")
    return 0

def get_klines(symbol, interval='15m', limit=20):
    print(f"🔍 Obteniendo klines para {symbol}...")
    result = bitunix_request('GET', '/api/v1/futures/market/kline', params={
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    })
    
    if result and result.get('code') == 0:
        klines = result.get('data', [])
        print(f"✅ Klines recibidos: {len(klines)}")
        return klines
    return []

def calculate_atr_from_klines(klines, period=14):
    if len(klines) < period + 1:
        print(f"⚠️ No hay suficientes klines: {len(klines)}")
        return None
    
    true_ranges = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    
    atr = sum(true_ranges[-period:]) / period
    print(f"✅ ATR calculado: {atr}")
    return atr

def set_leverage(symbol, leverage):
    print(f"⚙️ Configurando apalancamiento {leverage}x para {symbol}...")
    # ENDPOINT CORREGIDO SEGÚN DOCUMENTACIÓN
    result = bitunix_request('POST', '/api/v1/futures/account/change_leverage', data={
        'symbol': symbol,
        'leverage': leverage,
        'marginCoin': 'USDT'
    }, auth=True)
    
    if result and result.get('code') == 0:
        print(f"✅ Apalancamiento configurado correctamente")
        return True
    else:
        print(f"⚠️ Error configurando apalancamiento: {result}")
        return False

def place_order(symbol, side, qty):
    print(f"📤 Colocando orden {side} de {qty} {symbol}...")
    result = bitunix_request('POST', '/api/v1/futures/trade/place_order', data={
        'symbol': symbol,
        'qty': str(qty),
        'side': side,
        'tradeSide': 'OPEN',
        'orderType': 'MARKET'
    }, auth=True)
    
    if result and result.get('code') == 0:
        print(f"✅ Orden ejecutada: {result.get('data')}")
        return result.get('data')
    else:
        print(f"❌ Error en orden: {result}")
        return None

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
        
        # 1. Balance
        balance = get_balance()
        if balance <= 0:
            print("❌ Balance es 0 o error")
            return
        
        # 2. Apalancamiento
        set_leverage(symbol, LEVERAGE)
        
        # 3. ATR
        klines = get_klines(symbol, '15m', 20)
        atr = calculate_atr_from_klines(klines, 14)
        
        # 4. Riesgo
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        print(f"📊 SL: {params['sl']} | TP: {params['tp']}")
        
        # 5. Cantidad
        quantity = calculate_quantity(balance, float(entry_price), POSITION_SIZE_PERCENT, LEVERAGE)
        print(f"💰 Cantidad calculada: {quantity}")
        
        # 6. MODO PRUEBA (Descomenta las siguientes 3 líneas para operar de verdad)
        print("⚠️ MODO PRUEBA: Orden NO ejecutada. Para activar, descomenta place_order en el código.")
        # side = 'BUY' if action == 'LONG' else 'SELL'
        # order_result = place_order(symbol, side, quantity)
        # print(f"✅ Resultado: {order_result}")
        
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
