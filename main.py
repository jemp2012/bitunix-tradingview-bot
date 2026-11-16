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

# Fallback: offset porcentual para señales sin swing_price (formato antiguo)
RISK_CONFIG = {
    'BCH': {'sl_offset_pct': 0.6},
    'HYPE': {'sl_offset_pct': 0.7},
    'ATOM': {'sl_offset_pct': 0.8},
    'INJ': {'sl_offset_pct': 0.7},
    'default': {'sl_offset_pct': 0.8}
}

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE GESTIÓN DE TRADES
# ═════════════════════════════════════════════════════════════════════════════
TRADE_MANAGEMENT = {
    'be_activation_pct': 0.4,
    'fee_buffer_pct': 0.2,
    'tp1_pct': 1.0,
    'tp1_close_pct': 50.0,
    'tp2_pct': 2.0,
    'tp2_close_pct': 50.0,
    'trailing_activation_pct': 2.0,
    'trailing_distance_pct': 1.0,
    'check_interval': 10,
    # ═══ NUEVO: SL basado en mecha del SFP ═══
    'sfp_sl_buffer_pct': 0.15,   # Buffer sobre la mecha (0.15%)
    'sfp_sl_max_pct': 0.8,       # SL máximo si la mecha está muy cerca del entry
    'sfp_sl_min_pct': 0.15       # SL mínimo para evitar SL demasiado ajustado
}

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

def set_leverage(symbol, leverage):
    result = bitunix_request('POST', '/api/v1/futures/account/change_leverage', data={
        'symbol': symbol, 'leverage': leverage, 'marginCoin': 'USDT'
    }, auth=True)
    return result and result.get('code') == 0

def place_order(symbol, side, qty):
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
    
    for sym in symbol_variations:
        for qty_str in qty_variations:
            if float(qty_str) <= 0:
                continue
            
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

def close_partial_position(symbol, side, qty):
    print(f"📤 Cerrando posición parcial: {qty} {symbol} ({side})...")
    
    current_pos_id, current_qty = get_position_id(symbol)
    if current_pos_id is None or current_qty <= 0:
        print(f"⚠️ No hay posición activa para cerrar")
        return None
    
    if current_qty < qty:
        qty = current_qty
    
    symbol_variations = [symbol]
    
    qty_variations = [
        str(qty),
        f"{qty:.2f}",
        f"{qty:.3f}",
        str(int(qty)) if qty >= 1 else str(qty),
    ]
    
    for sym in symbol_variations:
        for qty_str in qty_variations:
            if float(qty_str) <= 0:
                continue
            
            data = {
                'symbol': sym,
                'qty': qty_str,
                'side': side,
                'tradeSide': 'CLOSE',
                'orderType': 'MARKET',
                'positionId': str(current_pos_id),
                'reduceOnly': True
            }
            
            result = bitunix_request('POST', '/api/v1/futures/trade/place_order', 
                                    data=data, auth=True, max_retries=1)
            
            if result and result.get('code') == 0:
                print(f"✅ ¡Cierre parcial ejecutado con {sym} qty={qty_str}!")
                return result.get('data')
            else:
                error_msg = result.get('msg', 'Unknown') if result else 'Sin respuesta'
                print(f"   ❌ Error: {error_msg}")
                
                if 'not allowed to trade' in error_msg:
                    break
    
    print(f"\n❌ Ninguna combinación de cierre funcionó")
    return None

def get_position_id(symbol):
    print(f"🔍 Buscando position ID para {symbol}...")
    
    symbol_variations = [
        symbol,
        symbol.replace('USDT', '-USDT'),
        symbol.replace('USDT', '_USDT'),
    ]
    
    result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
    
    if not result or result.get('code') != 0:
        return None, 0
    
    data = result.get('data', [])
    if not isinstance(data, list):
        return None, 0
    
    for pos in data:
        pos_symbol = pos.get('symbol', '')
        
        if pos_symbol in symbol_variations:
            position_id = pos.get('positionId')
            
            position_qty = (
                pos.get('positionQty') or 
                pos.get('qty') or 
                pos.get('size') or 
                pos.get('holdQty') or 
                pos.get('positionSize') or 
                0
            )
            position_qty = float(position_qty) if position_qty else 0.0
            
            print(f"✅ Position ID encontrado: {position_id} (símbolo: {pos_symbol}, qty: {position_qty})")
            
            if position_qty > 0:
                return position_id, position_qty
    
    print(f"⚠️ No se encontró posición activa para {symbol}")
    return None, 0

def get_pending_tpsl_orders(symbol, position_id):
    result = bitunix_request('GET', '/api/v1/futures/tpsl/get_pending_orders', 
                            params={'symbol': symbol}, auth=True)
    
    if result and result.get('code') == 0:
        data = result.get('data', [])
        if isinstance(data, list):
            for order in data:
                if str(order.get('positionId')) == str(position_id):
                    return order
    return None

def cancel_tpsl_order(symbol, order_id):
    result = bitunix_request('POST', '/api/v1/futures/tpsl/cancel_order', data={
        'symbol': symbol,
        'orderId': str(order_id)
    }, auth=True)
    
    return result and result.get('code') == 0

def get_price_precision(price):
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
    print(f"🛡️ Colocando SL en {sl_price} para {symbol}...")
    
    precision = get_price_precision(sl_price)
    sl_price_rounded = round(sl_price, precision)
    
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
        print(f"✅ Stop Loss colocado: {sl_price_rounded}")
        return result.get('data')
    else:
        print(f"⚠️ Error colocando SL: {result}")
        return None

def get_current_price(symbol):
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
# MONITOREO CON PARCIALES Y TRAILING
# ═════════════════════════════════════════════════════════════════════════════
def monitor_position(symbol, entry_price, action, initial_sl, position_id, initial_qty):
    print(f"\n👁️ Monitoreo iniciado: {symbol} {action}")
    print(f"   Entry: {entry_price} | SL inicial: {initial_sl} | Qty inicial: {initial_qty}")
    print(f"   BE: {TRADE_MANAGEMENT['be_activation_pct']}% | TP1: {TRADE_MANAGEMENT['tp1_pct']}% | TP2: {TRADE_MANAGEMENT['tp2_pct']}%")
    
    if initial_qty <= 0:
        print(f"⚠️ Qty inicial es 0, esperando 5s...")
        time.sleep(5)
        _, initial_qty = get_position_id(symbol)
        if initial_qty <= 0:
            print(f"❌ No se pudo obtener qty inicial. Deteniendo monitoreo.")
            return
    
    fee_buffer = TRADE_MANAGEMENT['fee_buffer_pct'] / 100
    if action == 'LONG':
        be_price = entry_price * (1 + fee_buffer)
    else:
        be_price = entry_price * (1 - fee_buffer)
    
    print(f"   📍 BE price: {be_price}")
    
    be_activated = False
    tp1_activated = False
    tp2_activated = False
    trailing_activated = False
    highest_price = entry_price
    lowest_price = entry_price
    check_count = 0
    consecutive_errors = 0
    max_consecutive_errors = 3
    zero_qty_count = 0
    
    close_side = 'SELL' if action == 'LONG' else 'BUY'
    
    while True:
        try:
            current_position_id, current_qty = get_position_id(symbol)
            
            if current_position_id is None:
                print(f"✅ Posición {symbol} cerrada (no encontrada). Deteniendo monitoreo.")
                break
            
            if current_qty <= 0:
                zero_qty_count += 1
                if zero_qty_count >= 3:
                    break
                time.sleep(TRADE_MANAGEMENT['check_interval'])
                continue
            
            zero_qty_count = 0
            
            current_price = get_current_price(symbol)
            if not current_price:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    break
                time.sleep(TRADE_MANAGEMENT['check_interval'])
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
                print(f"📊 [{check_count}] Precio: {current_price} | PnL: {pnl_pct:.2f}% | Qty: {current_qty}")
            
            # BE al 0.4%
            if pnl_pct >= TRADE_MANAGEMENT['be_activation_pct'] and not be_activated:
                print(f"\n🛡️ ¡BE ACTIVADO! PnL: {pnl_pct:.2f}%")
                be_result = place_stop_loss(symbol, current_position_id, be_price)
                if be_result:
                    print(f"   ✅ BE colocado en {be_price}")
                be_activated = True
            
            # TP1 al 1.0%
            if pnl_pct >= TRADE_MANAGEMENT['tp1_pct'] and be_activated and not tp1_activated:
                print(f"\n🎯 ¡TP1 ACTIVADO! PnL: {pnl_pct:.2f}%")
                qty_to_close = initial_qty * (TRADE_MANAGEMENT['tp1_close_pct'] / 100)
                qty_to_close = round(qty_to_close, get_price_precision(current_price))
                close_result = close_partial_position(symbol, close_side, qty_to_close)
                if close_result:
                    print(f"   ✅ TP1 ejecutado: {qty_to_close} {symbol}")
                tp1_activated = True
            
            # TP2 al 2.0%
            if pnl_pct >= TRADE_MANAGEMENT['tp2_pct'] and tp1_activated and not tp2_activated:
                print(f"\n🎯 ¡TP2 ACTIVADO! PnL: {pnl_pct:.2f}%")
                qty_to_close = current_qty * (TRADE_MANAGEMENT['tp2_close_pct'] / 100)
                qty_to_close = round(qty_to_close, get_price_precision(current_price))
                close_result = close_partial_position(symbol, close_side, qty_to_close)
                if close_result:
                    print(f"   ✅ TP2 ejecutado: {qty_to_close} {symbol}")
                    tp2_activated = True
                    trailing_activated = True
            
            # Trailing
            if trailing_activated:
                if action == 'LONG':
                    new_sl = highest_price * (1 - TRADE_MANAGEMENT['trailing_distance_pct'] / 100)
                    if new_sl > be_price:
                        place_stop_loss(symbol, current_position_id, new_sl)
                else:
                    new_sl = lowest_price * (1 + TRADE_MANAGEMENT['trailing_distance_pct'] / 100)
                    if new_sl < be_price:
                        place_stop_loss(symbol, current_position_id, new_sl)
            
            time.sleep(TRADE_MANAGEMENT['check_interval'])
            
        except Exception as e:
            print(f"⚠️ Error en monitoreo: {e}")
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                break
            time.sleep(TRADE_MANAGEMENT['check_interval'])
    
    print(f"🏁 Monitoreo finalizado para {symbol}")

# ════════════════════════════════════════════════════════════════════════════
# TRADING
# ═════════════════════════════════════════════════════════════════════════════
def convert_symbol(symbol):
    if '.P' in symbol:
        symbol = symbol.replace('.P', '')
    
    symbol_map = {
        'ZAMAUSDT': 'ZAMALUSDT',
    }
    
    return symbol_map.get(symbol, symbol)

def calculate_sl_from_sfp(action, entry_price, swing_price):
    """
    Calcula el SL basado en la mecha del SFP + buffer pequeño.
    
    Para SHORT: SL = swing_price × (1 + buffer)  [arriba de la mecha]
    Para LONG:  SL = swing_price × (1 - buffer)  [debajo de la mecha]
    
    Protecciones:
    - SL mínimo: 0.15% desde entry (evita SL demasiado ajustado)
    - SL máximo: 0.80% desde entry (evita SL demasiado amplio)
    """
    buffer = TRADE_MANAGEMENT['sfp_sl_buffer_pct'] / 100
    min_sl = TRADE_MANAGEMENT['sfp_sl_min_pct'] / 100
    max_sl = TRADE_MANAGEMENT['sfp_sl_max_pct'] / 100
    
    if action == 'SHORT':
        # SL arriba de la mecha
        sl = swing_price * (1 + buffer)
        
        # Validaciones
        sl_pct_from_entry = (sl - entry_price) / entry_price
        
        # Si el SL calculado está POR DEBAJO del entry (mecha muy cerca), ajustar
        if sl <= entry_price:
            sl = entry_price * (1 + min_sl)
            print(f"   ⚠️ Mecha muy cerca del entry, SL mínimo aplicado: {sl:.6f}")
        # Si el SL está demasiado lejos, limitar
        elif sl_pct_from_entry > max_sl:
            sl = entry_price * (1 + max_sl)
            print(f"   ⚠️ SL demasiado amplio, limitado a {max_sl*100}%: {sl:.6f}")
        # Si el SL está muy cerca (menos del mínimo), ampliar
        elif sl_pct_from_entry < min_sl:
            sl = entry_price * (1 + min_sl)
            print(f"   ⚠️ SL muy ajustado, ampliado a {min_sl*100}%: {sl:.6f}")
    else:  # LONG
        # SL debajo de la mecha
        sl = swing_price * (1 - buffer)
        
        sl_pct_from_entry = (entry_price - sl) / entry_price
        
        if sl >= entry_price:
            sl = entry_price * (1 - min_sl)
            print(f"   ⚠️ Mecha muy cerca del entry, SL mínimo aplicado: {sl:.6f}")
        elif sl_pct_from_entry > max_sl:
            sl = entry_price * (1 - max_sl)
            print(f"   ⚠️ SL demasiado amplio, limitado a {max_sl*100}%: {sl:.6f}")
        elif sl_pct_from_entry < min_sl:
            sl = entry_price * (1 - min_sl)
            print(f"   ⚠️ SL muy ajustado, ampliado a {min_sl*100}%: {sl:.6f}")
    
    return round(sl, 6)

def get_risk_params(symbol, entry_price, action, swing_price=None):
    """
    Calcula SL y TP.
    - Si hay swing_price: SL basado en mecha del SFP (nuevo método)
    - Si no hay swing_price: SL basado en offset porcentual (método antiguo, fallback)
    """
    if swing_price is not None and swing_price > 0:
        sl = calculate_sl_from_sfp(action, entry_price, swing_price)
        method = "SFP-MECHA"
    else:
        risk = RISK_CONFIG.get(symbol, RISK_CONFIG['default'])
        offset_pct = risk['sl_offset_pct'] / 100
        if action == 'LONG':
            sl = entry_price * (1 - offset_pct)
        else:
            sl = entry_price * (1 + offset_pct)
        method = "OFFSET"
    
    sl_distance = abs(entry_price - sl)
    tp_distance = sl_distance * 2.0
    
    if action == 'LONG':
        tp = entry_price + tp_distance
    else:
        tp = entry_price - tp_distance
    
    print(f"   📐 Método SL: {method} | SL: {round(sl, 6)} | TP: {round(tp, 6)}")
    
    return {
        'sl': round(sl, 6),
        'tp': round(tp, 6)
    }

def calculate_quantity(balance, entry_price, percent, leverage):
    usdt_to_use = balance * (percent / 100)
    buying_power = usdt_to_use * leverage
    quantity = buying_power / entry_price
    return round(quantity, 3)

def execute_order(action, raw_symbol, entry_price, swing_price=None):
    try:
        print(f"\n{'='*60}")
        print(f"🚀 Ejecución: {action} {raw_symbol} @ {entry_price}")
        if swing_price:
            print(f"   📍 SFP Mecha: {swing_price}")
        print(f"{'='*60}\n")
        
        symbol = convert_symbol(raw_symbol)
        print(f"✅ Símbolo: {raw_symbol} -> {symbol}")
        
        existing_pos_id, existing_qty = get_position_id(symbol)
        if existing_pos_id is not None and existing_qty > 0:
            print(f"⚠️ Ya existe una posición activa para {symbol} ({existing_qty})")
            return
        
        balance = get_balance()
        if balance <= 0:
            print("❌ Balance 0")
            return
        
        set_leverage(symbol, LEVERAGE)
        
        risk_params = get_risk_params(symbol, float(entry_price), action, swing_price)
        print(f"📊 SL: {risk_params['sl']} | TP: {risk_params['tp']}")
        
        quantity = calculate_quantity(balance, float(entry_price), POSITION_SIZE_PERCENT, LEVERAGE)
        print(f"💰 Cantidad: {quantity}")
        
        print("🚀 EJECUTANDO ORDEN...")
        side = 'BUY' if action == 'LONG' else 'SELL'
        order_result, used_symbol = place_order(symbol, side, quantity)
        
        if order_result:
            print("⏳ Esperando 2s...")
            time.sleep(2)
            
            actual_symbol = used_symbol or symbol
            position_id, position_qty = get_position_id(actual_symbol)
            if position_id:
                print(f"✅ Position ID: {position_id} | Qty: {position_qty}")
                sl_result = place_stop_loss(actual_symbol, position_id, risk_params['sl'])
                
                if sl_result:
                    print(f"👁️ Iniciando monitoreo...")
                    monitor_thread = threading.Thread(
                        target=monitor_position,
                        args=(actual_symbol, float(entry_price), action, risk_params['sl'], position_id, position_qty),
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

def process_order(action, symbol, price, swing_price=None):
    print(f"\n🔔 Process: {action} {symbol} {price} (swing: {swing_price})")
    if not BITUNIX_API_KEY:
        print("❌ Faltan API Keys")
        return
    execute_order(action, symbol, float(price), swing_price)

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
                # Formatos soportados:
                # Nuevo (6 campos): SFP|CONFIRMATION|SHORT|SYMBOL|entry|swing
                # Antiguo (5 campos): SFP|CONFIRMATION|SHORT|SYMBOL|entry
                
                if len(parts) < 5:
                    print(f"⚠️ Formato SFP incompleto: {message}")
                    return jsonify({'status': 'error', 'message': 'Formato SFP incompleto'}), 400
                
                sfp_type = parts[1].upper()
                action = parts[2].upper()
                symbol = parts[3].strip()
                price = parts[4].strip()
                swing_price = parts[5].strip() if len(parts) >= 6 else None
                
                if SFP_ALERT_MODE == 'BOTH' or SFP_ALERT_MODE == sfp_type:
                    print(f"✅ SFP {sfp_type} - Acción: {action} {symbol} @ {price} (swing: {swing_price})")
                    threading.Thread(target=process_order, args=(action, symbol, price, swing_price)).start()
                    return jsonify({'status': 'success', 'type': sfp_type}), 200
                else:
                    print(f"ℹ️ SFP {sfp_type} ignorado")
                    return jsonify({'status': 'ignored', 'type': sfp_type}), 200
            
            elif parts[0].upper() in ['LONG', 'SHORT']:
                action, symbol, price = parts[0].strip().upper(), parts[1].strip(), parts[2].strip()
                print(f"✅ Acción (legacy): {action}")
                threading.Thread(target=process_order, args=(action, symbol, price, None)).start()
                return jsonify({'status': 'success'}), 200
        
        return jsonify({'status': 'error', 'message': 'Formato no reconocido'}), 400
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
