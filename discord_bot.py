import discord
from discord.ext import commands, tasks
import re
import os
import time
import hashlib
import requests
import secrets
import json

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════════════════
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '')
DISCORD_CHANNEL_ID = int(os.environ.get('DISCORD_CHANNEL_ID', '0'))
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')
BITUNIX_BASE_URL = 'https://fapi.bitunix.com'

POSITION_SIZE_PERCENT = 1.0
DEFAULT_LEVERAGE = 20
FEE_BUFFER_PCT = 0.15
CHECK_INTERVAL = 10

print(f"🔧 Configuración: Channel={DISCORD_CHANNEL_ID}, Leverage={DEFAULT_LEVERAGE}", flush=True)

# ════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN BITUNIX
# ════════════════════════════════════════════════════════════════════════════
def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def generate_signature(secret_key, timestamp, nonce, api_key, query_string='', body=''):
    message = f"{nonce}{timestamp}{api_key}{query_string}{body}"
    digest = sha256_hex(message)
    return sha256_hex(f"{digest}{secret_key}")

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
                    query_string = ''.join([f"{k}{v}" for k, v in sorted(params.items())])
                if data:
                    body_str = json.dumps(data, separators=(',', ':'), sort_keys=True)
                headers['sign'] = generate_signature(BITUNIX_SECRET_KEY, timestamp, nonce, BITUNIX_API_KEY, query_string, body_str)
            
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=10)
            else:
                response = requests.post(url, data=body_str, headers=headers, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    return result
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Error request: {e}", flush=True)
            time.sleep(2)
    return None

# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES DE TRADING
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

def set_leverage(symbol, leverage):
    result = bitunix_request('POST', '/api/v1/futures/account/change_leverage', 
                            data={'symbol': symbol, 'leverage': leverage, 'marginCoin': 'USDT'}, auth=True)
    return result and result.get('code') == 0

def get_price_precision(price):
    if price >= 1000: return 2
    elif price >= 100: return 2
    elif price >= 10: return 2
    elif price >= 1: return 3
    elif price >= 0.1: return 4
    elif price >= 0.01: return 5
    elif price >= 0.001: return 6
    else: return 7

def place_order(symbol, side, qty):
    print(f"📤 Colocando orden {side} de {qty} {symbol}...", flush=True)
    symbol_variations = [symbol, symbol.replace('USDT', '-USDT'), symbol.replace('USDT', '_USDT')]
    qty_variations = [str(qty), f"{qty:.2f}", f"{qty:.3f}", f"{qty:.4f}", str(round(qty, 2)), str(int(qty))]
    
    for sym in symbol_variations:
        for qty_str in qty_variations:
            if float(qty_str) <= 0: continue
            data = {'symbol': sym, 'qty': qty_str, 'side': side, 'tradeSide': 'OPEN', 'orderType': 'MARKET'}
            result = bitunix_request('POST', '/api/v1/futures/trade/place_order', data=data, auth=True, max_retries=1)
            if result and result.get('code') == 0:
                print(f"✅ ¡ORDEN EJECUTADA con {sym} qty={qty_str}!", flush=True)
                return result.get('data'), sym
            else:
                error_msg = result.get('msg', 'Unknown') if result else 'Sin respuesta'
                if 'not allowed to trade' in error_msg: break
    print(f"❌ Ninguna combinación de orden funcionó", flush=True)
    return None, None

def get_position_id(symbol):
    symbol_variations = [symbol, symbol.replace('USDT', '-USDT'), symbol.replace('USDT', '_USDT')]
    result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
    if result and result.get('code') == 0:
        for pos in result.get('data', []):
            if pos.get('symbol', '') in symbol_variations:
                pos_qty = float(pos.get('positionQty') or pos.get('qty') or pos.get('size') or 0)
                if pos_qty > 0:
                    return pos.get('positionId'), pos_qty
    return None, 0

def get_pending_tpsl_orders(symbol, position_id):
    result = bitunix_request('GET', '/api/v1/futures/tpsl/get_pending_orders', params={'symbol': symbol}, auth=True)
    if result and result.get('code') == 0:
        for order in result.get('data', []):
            if str(order.get('positionId')) == str(position_id):
                return order
    return None

def cancel_tpsl_order(symbol, order_id):
    result = bitunix_request('POST', '/api/v1/futures/tpsl/cancel_order', data={'symbol': symbol, 'orderId': str(order_id)}, auth=True)
    return result and result.get('code') == 0

def place_stop_loss(symbol, position_id, sl_price, max_attempts=5):
    print(f"🛡️ Colocando SL en {sl_price} para {symbol}...", flush=True)
    precision = get_price_precision(sl_price)
    sl_price_rounded = round(sl_price, precision)
    existing_order = get_pending_tpsl_orders(symbol, position_id)
    if existing_order and existing_order.get('orderId'):
        cancel_tpsl_order(symbol, existing_order.get('orderId'))
        time.sleep(1)
    data = {'symbol': symbol, 'positionId': str(position_id), 'slPrice': str(sl_price_rounded)}
    for attempt in range(max_attempts):
        result = bitunix_request('POST', '/api/v1/futures/tpsl/position/place_order', data=data, auth=True, max_retries=1)
        if result and result.get('code') == 0:
            print(f"✅ Stop Loss colocado: {sl_price_rounded} (intento {attempt+1})", flush=True)
            return True
        print(f"   ⚠️ Intento {attempt+1}/{max_attempts} falló", flush=True)
        time.sleep(3)
    print(f"❌ SL NO colocado tras {max_attempts} intentos", flush=True)
    return False

def calculate_quantity(balance, entry_price, percent, leverage):
    usdt_to_use = balance * (percent / 100)
    buying_power = usdt_to_use * leverage
    return round(buying_power / entry_price, 3)

def get_current_price(symbol):
    for endpoint, params in [
        ('/api/v1/futures/market/kline', {'symbol': symbol, 'interval': '1m', 'limit': 1}),
        ('/api/v1/futures/market/ticker', {'symbol': symbol}),
    ]:
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
# PARSER DE SEÑALES
# ════════════════════════════════════════════════════════════════════════════
def parse_signal(message_content):
    try:
        lines = message_content.strip().split('\n')
        title = lines[0].strip()
        match = re.search(r'[!#]\s*([A-Za-z0-9]+)/([A-Za-z0-9]+)\s*[-–]\s*(Long|Short)', title, re.IGNORECASE)
        if not match:
            return None
        symbol = f"{match.group(1)}{match.group(2)}"
        direction = match.group(3).upper()
        data = {'symbol': symbol, 'direction': direction, 'entry': None, 'sl': None,
                'leverage': DEFAULT_LEVERAGE, 'tps': []}
        tp_dict = {}
        for line in lines[1:]:
            line_lower = line.strip().lower()
            if 'entrada' in line_lower or 'entry' in line_lower:
                num = re.search(r'[\d.]+', line)
                if num: data['entry'] = float(num.group())
            elif 'stop loss' in line_lower or ' sl ' in line_lower or line_lower.startswith('sl'):
                num = re.search(r'[\d.]+', line)
                if num: data['sl'] = float(num.group())
            elif 'apalancamiento' in line_lower or 'leverage' in line_lower:
                num = re.search(r'x?(\d+)', line, re.IGNORECASE)
                if num: data['leverage'] = int(num.group(1))
            elif 'objetivo' in line_lower or 'target' in line_lower or 'tp' in line_lower:
                idx = re.search(r'(?:objetivo|target|tp)\s*(\d+)', line_lower)
                price = re.search(r'([\d.]+)\s*$', line.strip())
                if idx and price:
                    tp_dict[int(idx.group(1))] = float(price.group(1))
        data['tps'] = [tp_dict[k] for k in sorted(tp_dict.keys())]
        if data['entry'] and data['sl']:
            return data
        return None
    except Exception as e:
        print(f"❌ Error parseando: {e}", flush=True)
        return None

# ════════════════════════════════════════════════════════════════════════════
# BOT CON discord.ext.commands (Reconexión Robusta)
# ════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Almacén de monitores activos
active_monitors = {}

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}", flush=True)
    print(f"📡 Escuchando canal ID: {DISCORD_CHANNEL_ID}", flush=True)
    # Iniciar tarea de monitoreo
    if not monitor_tps.is_running():
        monitor_tps.start()

@bot.event
async def on_message(message):
    if message.author == bot.user or message.channel.id != DISCORD_CHANNEL_ID:
        return
    if message.content.startswith('!'):
        print(f"📨 Señal detectada de {message.author.name}", flush=True)
        signal = parse_signal(message.content)
        if signal:
            await message.channel.send(f"🤖 Procesando: {signal['direction']} {signal['symbol']}")
            await execute_signal_async(signal)
        else:
            print("⚠️ No se pudo parsear la señal", flush=True)
    await bot.process_commands(message)

@bot.event
async def on_disconnect():
    print("⚠️ Bot desconectado. Reconectando...", flush=True)

@bot.event
async def on_resumed():
    print("✅ Bot reconectado", flush=True)

@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_tps():
    """Tarea periódica que monitorea todas las posiciones activas"""
    for symbol, monitor_data in list(active_monitors.items()):
        try:
            direction = monitor_data['direction']
            entry = monitor_data['entry']
            tps = monitor_data['tps']
            position_id = monitor_data['position_id']
            next_tp_index = monitor_data['next_tp_index']
            
            # Verificar si la posición sigue abierta
            current_pos_id, current_qty = get_position_id(symbol)
            if current_pos_id is None:
                print(f"✅ Posición {symbol} cerrada. Monitor finalizado.", flush=True)
                del active_monitors[symbol]
                continue
            
            current_price = get_current_price(symbol)
            if not current_price:
                continue
            
            # Verificar si alcanzó el siguiente TP
            if next_tp_index < len(tps):
                tp = tps[next_tp_index]
                reached = (current_price >= tp) if direction == 'LONG' else (current_price <= tp)
                
                if reached:
                    be_price = entry * (1 + FEE_BUFFER_PCT / 100) if direction == 'LONG' else entry * (1 - FEE_BUFFER_PCT / 100)
                    new_sl = be_price if next_tp_index == 0 else tps[next_tp_index - 1]
                    print(f"\n🎯 ¡TP{next_tp_index + 1} ALCANZADO! ({tp}) → SL a {new_sl:.6f}", flush=True)
                    place_stop_loss(symbol, current_pos_id, new_sl)
                    monitor_data['next_tp_index'] = next_tp_index + 1
        except Exception as e:
            print(f"⚠️ Error en monitor de {symbol}: {e}", flush=True)

async def execute_signal_async(signal):
    """Ejecución asíncrona de la señal"""
    try:
        print(f"\n{'='*60}", flush=True)
        print(f"🚀 Ejecución: {signal['direction']} {signal['symbol']} @ {signal['entry']}", flush=True)
        print(f"🛡️ SL: {signal['sl']} | ⚡ Leverage: {signal['leverage']}x | 🎯 TPs: {signal.get('tps', [])}", flush=True)
        print(f"{'='*60}\n", flush=True)

        existing_id, existing_qty = get_position_id(signal['symbol'])
        if existing_id and existing_qty > 0:
            print(f"⚠️ Ya existe posición activa para {signal['symbol']}. Ignorando.", flush=True)
            return

        balance = get_balance()
        if balance <= 10:
            print("❌ Balance insuficiente (< 10 USDT)", flush=True)
            return

        set_leverage(signal['symbol'], signal['leverage'])
        qty = calculate_quantity(balance, signal['entry'], POSITION_SIZE_PERCENT, signal['leverage'])
        print(f"💰 Cantidad calculada: {qty}", flush=True)

        side = 'BUY' if signal['direction'] == 'LONG' else 'SELL'
        order_result, used_symbol = place_order(signal['symbol'], side, qty)

        if not order_result:
            print("❌ Orden NO ejecutada", flush=True)
            return

        print("⏳ Esperando 2s para confirmar posición...", flush=True)
        await asyncio.sleep(2)

        actual_symbol = used_symbol or signal['symbol']
        position_id, position_qty = get_position_id(actual_symbol)

        if not position_id:
            print("⚠️ Orden ejecutada pero NO se encontró la posición.", flush=True)
            return

        print(f"✅ Position ID: {position_id} | Qty: {position_qty}", flush=True)
        sl_ok = place_stop_loss(actual_symbol, position_id, signal['sl'])

        if sl_ok:
            print("✅ SEÑAL EJECUTADA Y PROTEGIDA CON SL", flush=True)
            if signal.get('tps'):
                active_monitors[actual_symbol] = {
                    'direction': signal['direction'],
                    'entry': signal['entry'],
                    'tps': signal['tps'],
                    'position_id': position_id,
                    'next_tp_index': 0
                }
                print(f"👁️ Monitor de TPs iniciado para {actual_symbol}", flush=True)
            else:
                print("ℹ️ La señal no tiene TPs. Solo SL inicial.", flush=True)
        else:
            print("🚨 PELIGRO: Posición abierta SIN Stop Loss.", flush=True)
    except Exception as e:
        print(f"❌ Error en execute_signal: {e}", flush=True)
        import traceback
        traceback.print_exc()

# ════════════════════════════════════════════════════════════════════════════
# SERVIDOR HTTP PARA RENDER
# ════════════════════════════════════════════════════════════════════════════
from flask import Flask
health_app = Flask(__name__)

@health_app.route('/', methods=['GET'])
def health_index():
    return 'Discord Bot Running!'

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    health_app.run(host='0.0.0.0', port=port, debug=False)

# ════════════════════════════════════════════════════════════════════════════
# INICIO
# ════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import asyncio
    if not DISCORD_TOKEN or not BITUNIX_API_KEY:
        print("❌ Faltan variables de entorno", flush=True)
        exit(1)
    
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    print("🔗 Iniciando bot...", flush=True)
    bot.run(DISCORD_TOKEN)
