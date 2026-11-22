import discord
import re
import os
import time
import hashlib
import requests
import secrets
import json
import threading

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════════════════
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '')
DISCORD_CHANNEL_ID = int(os.environ.get('DISCORD_CHANNEL_ID', '0'))

BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')
BITUNIX_BASE_URL = 'https://fapi.bitunix.com'

POSITION_SIZE_PERCENT = 1.0
DEFAULT_LEVERAGE = 5

print(f"🔧 Configuración:")
print(f"   Channel ID: {DISCORD_CHANNEL_ID}")
print(f"   Token length: {len(DISCORD_TOKEN)}")
print(f"   API Key length: {len(BITUNIX_API_KEY)}")

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
            print(f"⚠️ Error: {e}")
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

def place_order(symbol, side, qty):
    data = {'symbol': symbol, 'qty': str(qty), 'side': side, 'tradeSide': 'OPEN', 'orderType': 'MARKET'}
    result = bitunix_request('POST', '/api/v1/futures/trade/place_order', data=data, auth=True)
    if result and result.get('code') == 0:
        print(f"✅ Orden ejecutada: {symbol} {side} {qty}")
        return result.get('data')
    print(f"❌ Error orden: {result.get('msg') if result else 'Sin respuesta'}")
    return None

def place_stop_loss(symbol, position_id, sl_price):
    data = {'symbol': symbol, 'positionId': str(position_id), 'slPrice': str(sl_price)}
    result = bitunix_request('POST', '/api/v1/futures/tpsl/position/place_order', data=data, auth=True)
    if result and result.get('code') == 0:
        print(f"🛡️ SL colocado en: {sl_price}")
        return True
    print(f"⚠️ Error SL: {result.get('msg') if result else 'Sin respuesta'}")
    return False

def get_position_id(symbol):
    result = bitunix_request('GET', '/api/v1/futures/position/get_pending_positions', auth=True)
    if result and result.get('code') == 0:
        for pos in result.get('data', []):
            if pos.get('symbol') == symbol:
                return pos.get('positionId'), float(pos.get('positionQty', 0))
    return None, 0

def calculate_quantity(balance, entry_price, percent, leverage):
    usdt_to_use = balance * (percent / 100)
    buying_power = usdt_to_use * leverage
    return round(buying_power / entry_price, 4)

# ════════════════════════════════════════════════════════════════════════════
# PARSER
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
        
        data = {'symbol': symbol, 'direction': direction, 'entry': None, 'sl': None, 'leverage': DEFAULT_LEVERAGE}
        
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
                if num: data['leverage'] = int(num.group())
        
        if data['entry'] and data['sl']:
            return data
        return None
    except Exception as e:
        print(f"❌ Error parseando: {e}")
        return None

# ════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ════════════════════════════════════════════════════════════════════════════
def execute_signal(signal):
    print(f"\n🚀 Ejecutando: {signal['direction']} {signal['symbol']} @ {signal['entry']}")
    
    balance = get_balance()
    if balance <= 10:
        print("❌ Balance insuficiente")
        return
    
    set_leverage(signal['symbol'], signal['leverage'])
    qty = calculate_quantity(balance, signal['entry'], POSITION_SIZE_PERCENT, signal['leverage'])
    
    side = 'BUY' if signal['direction'] == 'LONG' else 'SELL'
    order = place_order(signal['symbol'], side, qty)
    
    if order:
        time.sleep(2)
        pos_id, pos_qty = get_position_id(signal['symbol'])
        if pos_id:
            place_stop_loss(signal['symbol'], pos_id, signal['sl'])
            print("✅ Señal ejecutada y protegida con SL")

# ════════════════════════════════════════════════════════════════════════════
# BOT DE DISCORD
# ════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.all()

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")
    print(f"📡 Escuchando canal ID: {DISCORD_CHANNEL_ID}")

@client.event
async def on_disconnect():
    print("⚠️ Desconectado")

@client.event
async def on_resumed():
    print("✅ Reconectado")

@client.event
async def on_message(message):
    print(f"📨 Mensaje: {message.author.name} | Canal: {message.channel.id} | Contenido: {message.content[:50]}")
    
    if message.author == client.user:
        return
    
    if message.channel.id != DISCORD_CHANNEL_ID:
        return
    
    if message.content.startswith('!'):
        print(f"📨 Señal detectada de {message.author.name}")
        signal = parse_signal(message.content)
        
        if signal:
            try:
                await message.channel.send(f"🤖 Procesando: {signal['direction']} {signal['symbol']}")
                print(f"🚀 Iniciando ejecución de señal...")
                execute_signal(signal)
                print(f"✅ Ejecución completada")
            except Exception as e:
                print(f"❌ ERROR EN EJECUCIÓN: {e}")
                import traceback
                traceback.print_exc()
                await message.channel.send(f"⚠️ Error al procesar la señal: {str(e)[:100]}")
        else:
            print("⚠️ No se pudo parsear la señal")

# ════════════════════════════════════════════════════════════════════════════
# SERVIDOR HTTP
# ════════════════════════════════════════════════════════════════════════════
from flask import Flask, jsonify

health_app = Flask(__name__)

@health_app.route('/', methods=['GET'])
def health_index():
    return 'OK'

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    health_app.run(host='0.0.0.0', port=port, debug=False)

# ════════════════════════════════════════════════════════════════════════════
# INICIO
# ════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("❌ Falta DISCORD_TOKEN")
        exit(1)
    
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    client.run(DISCORD_TOKEN)
