from flask import Flask, request, jsonify
import threading
import os
import time
import hmac
import hashlib
import requests

app = Flask(__name__)

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═════════════════════════════════════════════════════════════════════════════
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')

POSITION_SIZE_PERCENT = 1.0  # 1% del balance
LEVERAGE = 5  # 5x apalancamiento

# Base URL de Bitunix API
BITUNIX_BASE_URL = 'https://openapi.bitunix.com'

RISK_CONFIG = {
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES DE BITUNIX API
# ═════════════════════════════════════════════════════════════════════════════
def generate_signature(secret_key, timestamp, method, path, query_string=''):
    """Genera la firma HMAC SHA256 para autenticación"""
    message = f"{timestamp}{method}{path}{query_string}"
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
    
    headers = {
        'Content-Type': 'application/json',
        'X-BU-API-KEY': BITUNIX_API_KEY if auth else '',
        'X-BU-TIMESTAMP': timestamp if auth else '',
    }
    
    if auth:
        query_string = ''
        if params:
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        
        signature = generate_signature(BITUNIX_SECRET_KEY, timestamp, method, endpoint, query_string)
        headers['X-BU-SIGNATURE'] = signature
    
    try:
        if method == 'GET':
            response = requests.get(url, params=params, headers=headers, timeout=10)
        elif method == 'POST':
            response = requests.post(url, json=data, headers=headers, timeout=10)
        
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Error en API request: {e}")
        return None

def get_balance():
    """Obtiene el balance de la cuenta"""
    result = bitunix_request('GET', '/openApi/swap/v2/user/balance', auth=True)
    if result and result.get('code') == 0:
        data = result.get('data', {})
        # Buscar balance de USDT
        for item in data.get('list', []):
            if item.get('coin') == 'USDT':
                free = float(item.get('availableBalance', 0))
                print(f"💰 Balance USDT: {free:.2f}")
                return free
    print(f"⚠️ No se pudo obtener balance. Respuesta: {result}")
    return 0

def get_klines(symbol, interval='15m', limit=20):
    """Obtiene datos de velas para calcular ATR"""
    result = bitunix_request('GET', '/openApi/swap/v2/quote/klines', params={
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    })
    if result and result.get('code') == 0:
        return result.get('data', [])
    return []

def calculate_atr_from_klines(klines, period=14):
    """Calcula ATR desde datos de velas"""
    if len(klines) < period + 1:
        return None
    
    true_ranges = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)
    
    atr = sum(true_ranges[-period:]) / period
    return atr

def set_leverage(symbol, leverage):
    """Configura el apalancamiento"""
    result = bitunix_request('POST', '/openApi/swap/v2/trade/leverage', data={
        'symbol': symbol,
        'leverage': leverage
    }, auth=True)
    
    if result and result.get('code') == 0:
        print(f"⚙️ Apalancamiento configurado: {leverage}x para {symbol}")
        return True
    else:
        print(f"⚠️ No se pudo configurar apalancamiento: {result}")
        return False

def place_order(symbol, side, quantity):
    """Coloca una orden de mercado"""
    result = bitunix_request('POST', '/openApi/swap/v2/trade/order', data={
        'symbol': symbol,
        'side': side,  # 'BUY' para LONG, 'SELL' para SHORT
        'positionSide': 'BOTH',
        'type': 'MARKET',
        'quantity': quantity
    }, auth=True)
    
    return result

def place_stop_loss(symbol, side, quantity, stop_price):
    """Coloca una orden de Stop Loss"""
    result = bitunix_request('POST', '/openApi/swap/v2/trade/order', data={
        'symbol': symbol,
        'side': side,
        'positionSide': 'BOTH',
        'type': 'STOP_MARKET',
        'quantity': quantity,
        'stopPrice': stop_price,
        'closePosition': 'false'
    }, auth=True)
    
    return result

# ═════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE CONVERSIÓN Y CÁLCULO
# ═════════════════════════════════════════════════════════════════════════════
def convert_symbol(symbol):
    """Convierte ZECUSDT.P a ZECUSDT (formato Bitunix API)"""
    if '.P' in symbol:
        return symbol.replace('.P', '')
    return symbol

def get_risk_params(symbol, entry_price, atr):
    """Calcula Stop Loss y Take Profit"""
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
    """Calcula la cantidad de contratos"""
    usdt_to_use = balance * (percent / 100)
    buying_power = usdt_to_use * leverage
    quantity = buying_power / entry_price
    return round(quantity, 3)  # Ajustar precisión según el activo

# ═════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN DE ORDEN
# ═════════════════════════════════════════════════════════════════════════════
def execute_order(action, raw_symbol, entry_price):
    try:
        symbol = convert_symbol(raw_symbol)
        print(f" Símbolo: {raw_symbol} -> {symbol}")
        
        # 1. Obtener balance
        balance = get_balance()
        if balance <= 0:
            print("❌ Sin balance")
            return
        
        # 2. Configurar apalancamiento
        set_leverage(symbol, LEVERAGE)
        
        # 3. Obtener velas y calcular ATR
        klines = get_klines(symbol, '15m', 20)
        atr = calculate_atr_from_klines(klines, 14)
        print(f" ATR: {atr}")
        
        # 4. Calcular niveles de riesgo
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        print(f"📊 SL: {params['sl']} | TP: {params['tp']}")
        
        # 5. Calcular cantidad
        quantity = calculate_quantity(balance, float(entry_price), POSITION_SIZE_PERCENT, LEVERAGE)
        print(f"💰 Cantidad: {quantity}")
        
        # 6. Ejecutar orden (MODO PRUEBA - descomentar para producción)
        print(f"️ MODO PRUEBA: Orden NO ejecutada")
        
        # Para producción, descomentar estas líneas:
        # side = 'BUY' if action == 'LONG' else 'SELL'
        # order_result = place_order(symbol, side, quantity)
        # print(f"📤 Orden ejecutada: {order_result}")
        
        # 7. Colocar Stop Loss (MODO PRUEBA)
        # sl_side = 'SELL' if action == 'LONG' else 'BUY'
        # sl_result = place_stop_loss(symbol, sl_side, quantity, params['sl'])
        # print(f"🛡️ Stop Loss colocado: {sl_result}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def process_order(action, symbol, price):
    if not BITUNIX_API_KEY:
        print("❌ Faltan API Keys")
        return
    execute_order(action, symbol, float(price))

# ═════════════════════════════════════════════════════════════════════════════
# WEBHOOKS
# ═════════════════════════════════════════════════════════════════════════════
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
