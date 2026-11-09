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
    'ZEC': {'sl_offset_pct': 0.6},
    'SOL': {'sl_offset_pct': 0.6},
    'AVAX': {'sl_offset_pct': 0.6},
    'BCH': {'sl_offset_pct': 0.5},
    'AAVE': {'sl_offset_pct': 0.5},
    'ARB': {'sl_offset_pct': 0.6},
    'LDO': {'sl_offset_pct': 0.6},
    'ENS': {'sl_offset_pct': 0.6},
    'ZAMA': {'sl_offset_pct': 0.7},
    'HYPE': {'sl_offset_pct': 0.6},
    'PUMPFUN': {'sl_offset_pct': 0.8},
    'VVV': {'sl_offset_pct': 0.7},
    'ATOM': {'sl_offset_pct': 0.6},
    'BTW': {'sl_offset_pct': 0.6},
    'STX': {'sl_offset_pct': 0.6},
    'WLD': {'sl_offset_pct': 0.7},
    'LIGHTER': {'sl_offset_pct': 0.8},
    'INJ': {'sl_offset_pct': 0.6},
    'RAY': {'sl_offset_pct': 0.6},
    'default': {'sl_offset_pct': 0.6}
}

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE GESTIÓN DE TRADES
# ═════════════════════════════════════════════════════════════════════════════
TRADE_MANAGEMENT = {
    'be_activation_pct': 1.0,
    'fee_buffer_pct': 0.2,
    'tp1_pct': 1.0,
    'tp1_close_pct': 50.0,
    'tp2_pct': 2.0,
    'tp2_close_pct': 50.0,
    'trailing_activation_pct': 2.0,
    'trailing_distance_pct': 1.0,
    'check_interval': 10
}

# ════════════════════════════════════════════════════════════════════════════
# SFP ALERT MODE
# ════════════════════════════════════════════════════════════════════════════
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

def close_partial_position(symbol, side, qty):
    """Cierra una posición parcialmente usando el positionId requerido por Bitunix"""
    print(f"📤 Cerrando posición parcial: {qty} {symbol} ({side})...")
    
    current_pos_id, current_qty = get_position_id(symbol)
    if current_pos_id is None or current_qty <= 0:
        print(f"⚠️ No hay posición activa para cerrar")
        return None
    
    if current_qty < qty:
        print(f"⚠️ Cantidad solicitada ({qty}) mayor que posición ({current_qty}), ajustando")
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
            
            print(f"\n🔄 Probando cierre: {sym} con qty={qty_str}")
            
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
                error_code = result.get('code', 'N/A') if result else 'N/A'
                print(f"   ❌ Error {error_code}: {error_msg}")
                
                if 'not allowed to trade' in error_msg:
                    break
    
    print(f"\n❌ Ninguna combinación de cierre funcionó")
    return None

def get_position_id(symbol):
    """Obtiene el ID y cantidad de la posición activa para un símbolo"""
    print(f"🔍 Buscando position ID para {symbol}...")
    
    symbol_variations = [
        symbol,
        symbol.replace('USDT', '-USDT'),
        symbol.replace('USDT', '_USDT'),
    ]
    
    result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
    
    if not result or result.get('code') != 0:
        print(f"⚠️ Error obteniendo posiciones: {result}")
        return None, 0
    
    data = result.get('data', [])
    if not isinstance(data, list):
        print(f"⚠️ Formato de datos inesperado: {type(data)}")
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
    """Obtiene el precio actual"""
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
    print(f"   TP1: {TRADE_MANAGEMENT['tp1_pct']}% (cerrar {TRADE_MANAGEMENT['tp1_close_pct']}%)")
    print(f"   TP2: {TRADE_MANAGEMENT['tp2_pct']}% (cerrar {TRADE_MANAGEMENT['tp2_close_pct']}% restante)")
    print(f"   Trailing: {TRADE_MANAGEMENT['trailing_distance_pct']}% después de TP2")
    print(f"   Fee Buffer: {TRADE_MANAGEMENT['fee_buffer_pct']}% (para cubrir fees en BE)")
    
    if initial_qty <= 0:
        print(f"⚠️ Qty inicial es 0, esperando 5s...")
        time.sleep(5)
        _, initial_qty = get_position_id(symbol)
        if initial_qty <= 0:
            print(f"❌ No se pudo obtener qty inicial. Deteniendo monitoreo.")
            return
    
    # Calcular el precio de BE con buffer de fees
    fee_buffer = TRADE_MANAGEMENT['fee_buffer_pct'] / 100
    if action == 'LONG':
        be_price = entry_price * (1 + fee_buffer)
    else:
        be_price = entry_price * (1 - fee_buffer)
    
    print(f"   📍 BE calculado: {be_price} (entry {entry_price} ± {TRADE_MANAGEMENT['fee_buffer_pct']}%)")
    
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
                print(f"⚠️ Qty=0 detectado ({zero_qty_count}/3). Reintentando...")
                if zero_qty_count >= 3:
                    print(f"❌ Qty=0 durante 3 checks consecutivos. Asumiendo posición cerrada.")
                    break
                time.sleep(TRADE_MANAGEMENT['check_interval'])
                continue
            
            zero_qty_count = 0
            
            current_price = get_current_price(symbol)
            if not current_price:
                print(f"⚠️ No se pudo obtener precio de {symbol}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    print(f"❌ Demasiados errores ({consecutive_errors}). Deteniendo monitoreo.")
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
                print(f"📊 [{check_count}] Precio: {current_price} | PnL: {pnl_pct:.2f}% | Qty: {current_qty} | Highest: {highest_price} | Lowest: {lowest_price}")
            
            # TP1: BE + Cerrar 50% al 1%
            if pnl_pct >= TRADE_MANAGEMENT['tp1_pct'] and not tp1_activated:
                print(f"\n🎯 ¡TP1 ACTIVADO! PnL: {pnl_pct:.2f}% >= {TRADE_MANAGEMENT['tp1_pct']}%")
                
                print(f"   📍 Moviendo SL a BE: {be_price} (entry {entry_price} + {TRADE_MANAGEMENT['fee_buffer_pct']}% fees)")
                be_result = place_stop_loss(symbol, current_position_id, be_price)
                
                if be_result:
                    print(f"   ✅ BE colocado exitosamente en {be_price}")
                else:
                    print(f"   ⚠️ No se pudo colocar BE")
                
                qty_to_close = initial_qty * (TRADE_MANAGEMENT['tp1_close_pct'] / 100)
                qty_to_close = round(qty_to_close, get_price_precision(current_price))
                
                print(f"   📤 Cerrando {TRADE_MANAGEMENT['tp1_close_pct']}% ({qty_to_close} {symbol})...")
                
                close_result = close_partial_position(symbol, close_side, qty_to_close)
                
                if close_result:
                    print(f"   ✅ TP1 ejecutado: {qty_to_close} {symbol} cerrados")
                else:
                    print(f"   ⚠️ No se pudo ejecutar cierre parcial")
                
                tp1_activated = True
            
            # TP2: Cerrar 50% del remanente + Activar Trailing al 2%
            if pnl_pct >= TRADE_MANAGEMENT['tp2_pct'] and tp1_activated and not tp2_activated:
                print(f"\n🎯 ¡TP2 ACTIVADO! PnL: {pnl_pct:.2f}% >= {TRADE_MANAGEMENT['tp2_pct']}%")
                
                qty_to_close = current_qty * (TRADE_MANAGEMENT['tp2_close_pct'] / 100)
                qty_to_close = round(qty_to_close, get_price_precision(current_price))
                
                print(f"   📤 Cerrando {TRADE_MANAGEMENT['tp2_close_pct']}% del remanente ({qty_to_close} {symbol})...")
                
                close_result = close_partial_position(symbol, close_side, qty_to_close)
                
                if close_result:
                    print(f"   ✅ TP2 ejecutado: {qty_to_close} {symbol} cerrados")
                    tp2_activated = True
                    trailing_activated = True
                    print(f"   🎯 Activando Trailing Stop al {TRADE_MANAGEMENT['trailing_distance_pct']}%")
                else:
                    print(f"   ❌ No se pudo ejecutar TP2")
            
            # Trailing Stop después de TP2
            if trailing_activated:
                if action == 'LONG':
                    new_sl = highest_price * (1 - TRADE_MANAGEMENT['trailing_distance_pct'] / 100)
                    if new_sl > be_price:
                        print(f"📈 Trailing: new_sl={new_sl:.4f} > be={be_price:.4f}, actualizando...")
                        sl_result = place_stop_loss(symbol, current_position_id, new_sl)
                        if sl_result:
                            print(f"   ✅ Trailing SL actualizado: {new_sl:.4f}")
                else:
                    new_sl = lowest_price * (1 + TRADE_MANAGEMENT['trailing_distance_pct'] / 100)
                    if new_sl < be_price:
                        print(f"📉 Trailing: new_sl={new_sl:.4f} < be={be_price:.4f}, actualizando...")
                        sl_result = place_stop_loss(symbol, current_position_id, new_sl)
                        if sl_result:
                            print(f"   ✅ Trailing SL actualizado: {new_sl:.4f}")
            
            time.sleep(TRADE_MANAGEMENT['check_interval'])
            
        except Exception as e:
            print(f"⚠️ Error en monitoreo: {e}")
            import traceback
            traceback.print_exc()
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                print(f"❌ Demasiados errores. Deteniendo monitoreo.")
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

def get_risk_params(symbol, entry_price, action):
    """Calcula SL basado en offset desde el entry (Opción B)"""
    risk = RISK_CONFIG.get(symbol, RISK_CONFIG['default'])
    offset_pct = risk['sl_offset_pct']
    
    # Calcular SL como offset desde el entry
    if action == 'LONG':
        sl = entry_price * (1 - offset_pct / 100)  # SL abajo del entry
    else:  # SHORT
        sl = entry_price * (1 + offset_pct / 100)  # SL arriba del entry
    
    # Calcular TP basado en R:R de 2:1
    sl_distance = abs(entry_price - sl)
    tp_distance = sl_distance * 2.0
    
    if action == 'LONG':
        tp = entry_price + tp_distance
    else:
        tp = entry_price - tp_distance
    
    return {
        'sl': round(sl, 6),
        'tp': round(tp, 6)
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
        
        existing_pos_id, existing_qty = get_position_id(symbol)
        if existing_pos_id is not None and existing_qty > 0:
            print(f"⚠️ Ya existe una posición activa para {symbol} ({existing_qty} unidades)")
            print(f"⚠️ No se abrirá una nueva posición")
            return
        
        balance = get_balance()
        if balance <= 0:
            print("❌ Balance 0")
            return
        
        set_leverage(symbol, LEVERAGE)
        
        # NUEVO: Calcular SL basado en offset (Opción B)
        risk_params = get_risk_params(symbol, float(entry_price), action)
        print(f"📊 SL inicial: {risk_params['sl']} ({RISK_CONFIG.get(symbol, RISK_CONFIG['default'])['sl_offset_pct']}% offset) | TP: {risk_params['tp']}")
        
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
                print(f"🛡️ Colocando SL inicial...")
                sl_result = place_stop_loss(actual_symbol, position_id, risk_params['sl'])
                
                if sl_result:
                    print(f"👁️ Iniciando monitoreo con parciales...")
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
