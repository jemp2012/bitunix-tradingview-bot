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

POSITION_SIZE_PERCENT = 1.0
LEVERAGE = 5

BITUNIX_BASE_URL = 'https://fapi.bitunix.com'

RISK_CONFIG = {
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'ARB': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'LDO': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'ENS': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'ZAMAL': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'HYPE': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'PUMPFUN': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

TRAILING_CONFIG = {
    'activation_pct': 1.0,
    'trail_pct': 0.5,
    'check_interval': 10
}

# ════════════════════════════════════════════════════════════════════════════
# SFP ALERT MODE
# ════════════════════════════════════════════════════════════════════════════
# 'CREATION': Opera inmediatamente cuando se detecta el SFP (más arriesgado, mejor precio)
# 'CONFIRMATION': Opera cuando se confirma el SFP al cerrar la vela (más seguro)
# 'BOTH': Opera en ambos momentos (no recomendado)
SFP_ALERT_MODE = os.environ.get('SFP_ALERT_MODE', 'CONFIRMATION')

# ════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN
# ═════════════════════════════════════════════════════════════════════════════
def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def generate_signature(secret_key, timestamp, nonce, api_key, query_string='', body=''):
    message = f"{nonce}{timestamp}{api_key}{query_string}{body}"
    digest = sha256_hex(message)
    sign = sha256_hex(f"{digest}{secret_key}")
    return sign

def bitunix_request(method, endpoint, params=None, data=None, auth=False, max_retries=3):
    url = f"{BITUNIX_BASE_URL}{endpoint}"
    
    for attempt in range(max_retries):
        try:
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
            
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, data=body_str, headers=headers, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    return result
                else:
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    return result
            else:
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
    return None

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE BITUNIX
# ════════════════════════════════════════════════════════════════════════════
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
    """Coloca orden probando múltiples formatos de símbolo y precisión"""
    print(f"📤 Colocando orden {side} de {qty} {symbol}...")
    
    symbol_variations = [
        symbol,
        symbol.replace('USDT', '-USDT'),
        symbol.replace('USDT', '_USDT'),
    ]
    
    qty_variations = [
        str(qty),
        f"{qty:.2f}",
        f"{qty:.3f}",
        f"{qty:.4f}",
        str(round(qty, 2)),
        str(int(qty)),
        str(int(qty) + 1),
        str(int(qty) + 2),
        str(int(qty) + 3),
        str(int(qty) + 5),
        str(int(qty) + 10),
    ]
    
    print(f"🔍 Probando {len(symbol_variations)} símbolos x {len(qty_variations)} cantidades...")
    
    for sym in symbol_variations:
        for qty_str in qty_variations:
            if float(qty_str) <= 0:
                continue
            
            print(f"\n🔄 Probando: {sym} con qty={qty_str}")
            
            data = {
                'symbol': sym,
                'qty': qty_str,
                'side': side,
                'tradeSide': 'OPEN',
                'orderType': 'MARKET'
            }
            
            result = bitunix_request('POST', '/api/v1/futures/trade/place_order', 
                                    data=data, auth=True, max_retries=1)
            
            if result and result.get('code') == 0:
                print(f"✅ ¡ORDEN EJECUTADA con {sym} qty={qty_str}!")
                return result.get('data'), sym
            else:
                error_msg = result.get('msg', 'Unknown') if result else 'Sin respuesta'
                print(f"   ❌ Error: {error_msg}")
                
                if 'not allowed to trade' in error_msg:
                    break
    
    print(f"\n❌ Ninguna combinación funcionó")
    return None, None

def get_position_id(symbol):
    """Obtiene el ID de la posición activa para un símbolo"""
    print(f"🔍 Buscando position ID para {symbol}...")
    
    symbol_variations = [
        symbol,
        symbol.replace('USDT', '-USDT'),
        symbol.replace('USDT', '_USDT'),
    ]
    
    for sym in symbol_variations:
        result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
        if result and result.get('code') == 0:
            data = result.get('data', [])
            if isinstance(data, list):
                for pos in data:
                    if pos.get('symbol') == sym:
                        position_id = pos.get('positionId')
                        print(f"✅ Position ID encontrado: {position_id} (símbolo: {sym})")
                        return position_id
    
    print(f"⚠️ No se encontró position ID para {symbol}")
    return None

def get_pending_tpsl_orders(symbol, position_id):
    """Obtiene las órdenes TP/SL pendientes para una posición específica"""
    print(f"🔍 Buscando órdenes TP/SL pendientes para {symbol}...")
    result = bitunix_request('GET', '/api/v1/futures/tpsl/get_pending_orders', 
                            params={'symbol': symbol}, auth=True)
    
    if result and result.get('code') == 0:
        data = result.get('data', [])
        if isinstance(data, list):
            for order in data:
                if str(order.get('positionId')) == str(position_id):
                    print(f"✅ Orden TP/SL encontrada: ID {order.get('orderId')}")
                    return order
    return None

def cancel_tpsl_order(symbol, order_id):
    """Cancela una orden TP/SL existente"""
    print(f"🗑️ Cancelando orden TP/SL {order_id}...")
    result = bitunix_request('POST', '/api/v1/futures/tpsl/cancel_order', data={
        'symbol': symbol,
        'orderId': str(order_id)
    }, auth=True)
    
    if result and result.get('code') == 0:
        print(f"✅ Orden TP/SL cancelada exitosamente")
        return True
    else:
        print(f"⚠️ Error cancelando orden: {result}")
        return False

def get_price_precision(price):
    """Determina la precisión decimal según el precio"""
    if price >= 1000:
        return 2
    elif price >= 100:
        return 2
    elif price >= 10:
        return 2
    elif price >= 1:
        return 3
    elif price >= 0.1:
        return 4
    elif price >= 0.01:
        return 5
    elif price >= 0.001:
        return 6
    else:
        return 7

def place_stop_loss(symbol, position_id, sl_price):
    """Coloca SL con precisión dinámica según el precio"""
    print(f"🛡️ Colocando SL en {sl_price} para {symbol} (Position ID: {position_id})...")
    
    precision = get_price_precision(sl_price)
    sl_price_rounded = round(sl_price, precision)
    
    print(f"📊 Precisión usada: {precision} decimales → SL: {sl_price_rounded}")
    
    existing_order = get_pending_tpsl_orders(symbol, position_id)
    if existing_order:
        order_id = existing_order.get('orderId')
        if order_id:
            cancel_tpsl_order(symbol, order_id)
            time.sleep(1)
    
    data = {
        'symbol': symbol,
        'positionId': str(position_id),
        'slPrice': str(sl_price_rounded)
    }
    
    result = bitunix_request('POST', '/api/v1/futures/tpsl/position/place_order', data=data, auth=True)
    
    if result and result.get('code') == 0:
        print(f"✅ Stop Loss colocado exitosamente: {sl_price_rounded}")
        return result.get('data')
    else:
        print(f"⚠️ Error colocando SL: {result}")
        return None

def get_current_price(symbol):
    """Obtiene el precio actual - versión optimizada"""
    endpoints_to_try = [
        ('/api/v1/futures/market/kline', {'symbol': symbol, 'interval': '1m', 'limit': 1}),
        ('/api/v1/futures/market/ticker', {'symbol': symbol}),
    ]
    
    for endpoint, params in endpoints_to_try:
        try:
            result = bitunix_request('GET', endpoint, params=params, auth=False, max_retries=1)
            
            if result and result.get('code') == 0:
                data = result.get('data', {})
                price = None
                
                if isinstance(data, list) and len(data) > 0:
                    item = data[0]
                    price = item.get('lastPrice') or item.get('price') or item.get('close')
                    if price is None and isinstance(item, list) and len(item) >= 5:
                        price = item[4]
                elif isinstance(data, dict):
                    price = data.get('lastPrice') or data.get('price') or data.get('close')
                
                if price is not None:
                    return float(price)
        except Exception:
            continue
    
    return None

# ════════════════════════════════════════════════════════════════════════════
# MONITOREO CON BE + TRAILING STOP
# ═════════════════════════════════════════════════════════════════════════════
def monitor_position(symbol, entry_price, action, initial_sl, position_id):
    print(f"\n👁️ Monitoreo iniciado: {symbol} {action}")
    print(f"   Entry: {entry_price} | SL inicial: {initial_sl}")
    print(f"   BE se activará al {TRAILING_CONFIG['activation_pct']}% de ganancia")
    
    trailing_activated = False
    be_activated = False
    highest_price = entry_price
    lowest_price = entry_price
    check_count = 0
    consecutive_errors = 0
    max_consecutive_errors = 3
    
    while True:
        try:
            current_position_id = get_position_id(symbol)
            if current_position_id is None:
                print(f"✅ Posición {symbol} cerrada. Deteniendo monitoreo.")
                break
            
            current_price = get_current_price(symbol)
            if not current_price:
                print(f"⚠️ No se pudo obtener precio de {symbol}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    print(f"❌ Demasiados errores ({consecutive_errors}). Deteniendo monitoreo.")
                    break
                time.sleep(TRAILING_CONFIG['check_interval'])
                continue
            
            consecutive_errors = 0
            
            if action == 'LONG':
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                if current_price > highest_price:
                    highest_price = current_price
            else:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
                if current_price < lowest_price:
                    lowest_price = current_price
            
            check_count += 1
            
            if check_count % 3 == 0:
                print(f"📊 [{check_count}] Precio: {current_price} | PnL: {pnl_pct:.2f}% | Highest: {highest_price} | Lowest: {lowest_price}")
            
            if pnl_pct >= TRAILING_CONFIG['activation_pct'] and not be_activated:
                print(f"\n🎯 ¡BE ACTIVADO! PnL: {pnl_pct:.2f}% >= {TRAILING_CONFIG['activation_pct']}%")
                print(f"   Moviendo SL al precio de entrada: {entry_price}")
                
                sl_result = place_stop_loss(symbol, position_id, entry_price)
                
                if sl_result:
                    print(f"✅ BE colocado exitosamente en {entry_price}")
                    be_activated = True
                    trailing_activated = True
                else:
                    print(f"❌ No se pudo colocar BE, reintentando en próximo ciclo...")
            
            if trailing_activated:
                if action == 'LONG':
                    new_sl = highest_price * (1 - TRAILING_CONFIG['trail_pct'] / 100)
                    if new_sl > entry_price:
                        print(f"📈 Trailing: new_sl={new_sl:.2f} > entry={entry_price}, actualizando...")
                        sl_result = place_stop_loss(symbol, position_id, new_sl)
                        if sl_result:
                            print(f"✅ Trailing SL actualizado: {new_sl:.2f}")
                else:
                    new_sl = lowest_price * (1 + TRAILING_CONFIG['trail_pct'] / 100)
                    if new_sl < entry_price:
                        print(f"📉 Trailing: new_sl={new_sl:.2f} < entry={entry_price}, actualizando...")
                        sl_result = place_stop_loss(symbol, position_id, new_sl)
                        if sl_result:
                            print(f"✅ Trailing SL actualizado: {new_sl:.2f}")
            
            time.sleep(TRAILING_CONFIG['check_interval'])
            
        except Exception as e:
            print(f"⚠️ Error en monitoreo: {e}")
            import traceback
            traceback.print_exc()
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                print(f"❌ Demasiados errores. Deteniendo monitoreo.")
                break
            time.sleep(TRAILING_CONFIG['check_interval'])
    
    print(f"🏁 Monitoreo finalizado para {symbol}")

# ════════════════════════════════════════════════════════════════════════════
# TRADING
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
        print(f"🚀 Ejecución: {action} {raw_symbol} @ {entry_price}")
        print(f"{'='*60}\n")
        
        symbol = convert_symbol(raw_symbol)
        print(f"✅ Símbolo: {raw_symbol} -> {symbol}")
        
        balance = get_balance()
        if balance <= 0:
            print("❌ Balance 0")
            return
        
        set_leverage(symbol, LEVERAGE)
        
        klines = get_klines(symbol, '15m', 20)
        atr = calculate_atr_from_klines(klines, 14)
        
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        print(f"📊 SL inicial: {params['sl']} | TP: {params['tp']}")
        
        quantity = calculate_quantity(balance, float(entry_price), POSITION_SIZE_PERCENT, LEVERAGE)
        print(f"💰 Cantidad: {quantity}")
        
        print("🚀 EJECUTANDO ORDEN...")
        side = 'BUY' if action == 'LONG' else 'SELL'
        order_result, used_symbol = place_order(symbol, side, quantity)
        
        if order_result:
            print("⏳ Esperando 2s...")
            time.sleep(2)
            
            actual_symbol = used_symbol or symbol
            position_id = get_position_id(actual_symbol)
            if position_id:
                print(f"✅ Position ID: {position_id}")
                print(f"🛡️ Colocando SL inicial...")
                sl_result = place_stop_loss(actual_symbol, position_id, params['sl'])
                
                if sl_result:
                    print(f"👁️ Iniciando monitoreo BE/Trailing...")
                    monitor_thread = threading.Thread(
                        target=monitor_position,
                        args=(actual_symbol, float(entry_price), action, params['sl'], position_id),
                        daemon=True
                    )
                    monitor_thread.start()
            else:
                print(f"⚠️ No se obtuvo positionId")
        else:
            print("❌ Orden NO ejecutada")
        
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def process_order(action, symbol, price):
    print(f"\n🔔 Process: {action} {symbol} {price}")
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
        message = request.get_data(as_text=True).strip()
        print(f"📨 Webhook recibido: {message}")
        
        parts = message.split('|')
        
        if len(parts) >= 3:
            if parts[0].upper() == 'SFP':
                if len(parts) < 5:
                    print(f"⚠️ Formato SFP incompleto: {message}")
                    return jsonify({'status': 'error', 'message': 'Formato SFP incompleto'}), 400
                
                sfp_type = parts[1].upper()
                action = parts[2].upper()
                symbol = parts[3].strip()
                price = parts[4].strip()
                
                if SFP_ALERT_MODE == 'BOTH' or SFP_ALERT_MODE == sfp_type:
                    print(f"✅ SFP {sfp_type} - Acción: {action} {symbol} @ {price}")
                    threading.Thread(target=process_order, args=(action, symbol, price)).start()
                    return jsonify({'status': 'success', 'type': sfp_type}), 200
                else:
                    print(f"ℹ️ SFP {sfp_type} ignorado (modo actual: {SFP_ALERT_MODE})")
                    return jsonify({'status': 'ignored', 'type': sfp_type}), 200
            
            elif parts[0].upper() in ['LONG', 'SHORT']:
                action, symbol, price = parts[0].strip().upper(), parts[1].strip(), parts[2].strip()
                print(f"✅ Acción (legacy): {action}")
                threading.Thread(target=process_order, args=(action, symbol, price)).start()
                return jsonify({'status': 'success'}), 200
        
        return jsonify({'status': 'error', 'message': 'Formato no reconocido'}), 400
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
