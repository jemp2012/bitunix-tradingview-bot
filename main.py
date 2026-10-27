from flask import Flask, request, jsonify
import threading
import os
import ccxt
import time

app = Flask(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE BITUNIX
# ═════════════════════════════════════════════════════════════════════════════
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE RIESGO
# ═════════════════════════════════════════════════════════════════════════════

# % del balance a usar por operación (EMPEZAR CON 1% PARA PRUEBAS)
POSITION_SIZE_PERCENT = 1.0  # 1% del balance por operación

# Apalancamiento (ajustar según tu tolerancia al riesgo)
LEVERAGE = 5  # 5x de apalancamiento

# Configuración de riesgo por activo (basado en nuestros tests)
RISK_CONFIG = {
    # Activos volátiles (perfil ZEC)
    'ZEC': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'SOL': {'sl_atr_mult': 2.5, 'tp_rr': 2.5},
    'AVAX': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    'FTM': {'sl_atr_mult': 2.0, 'tp_rr': 2.5},
    
    # Activos moderados (perfil BCH/AAVE)
    'BCH': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'AAVE': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'LINK': {'sl_atr_mult': 1.5, 'tp_rr': 2.0},
    'UNI': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'MATIC': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'ARBUSDT': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    'ENS': {'sl_atr_mult': 1.8, 'tp_rr': 2.0},
    
    # Default para otros activos
    'default': {'sl_atr_mult': 1.8, 'tp_rr': 2.0}
}

# ═════════════════════════════════════════════════════════════════════════════
# INICIALIZAR EXCHANGE
# ═════════════════════════════════════════════════════════════════════════════
def get_exchange():
    """Inicializa la conexión con Bitunix Futures"""
    exchange = ccxt.bitunix({
        'apiKey': BITUNIX_API_KEY,
        'secret': BITUNIX_SECRET_KEY,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',  # Mercado de Futuros Lineales
        }
    })
    exchange.load_markets()
    return exchange

# ═════════════════════════════════════════════════════════════════════════════
# OBTENER BALANCE
# ═════════════════════════════════════════════════════════════════════════════
def get_available_balance(exchange):
    """
    Obtiene el balance disponible en USDT
    """
    try:
        balance = exchange.fetch_balance()
        
        # Bitunix suele usar 'USDT' en el balance de futuros
        if 'USDT' in balance:
            available = balance['USDT'].get('free', 0)
            total = balance['USDT'].get('total', 0)
            print(f"💰 Balance USDT - Disponible: {available:.2f} | Total: {total:.2f}")
            return available
        else:
            print(f"⚠️ No se encontró balance USDT. Balances disponibles: {list(balance.keys())}")
            return 0
            
    except Exception as e:
        print(f"❌ Error obteniendo balance: {e}")
        return 0

# ═════════════════════════════════════════════════════════════════════════════
# CÁLCULO DE ATR
# ═════════════════════════════════════════════════════════════════════════════
def calculate_atr(exchange, symbol, timeframe='15m', period=14):
    """Calcula el ATR del símbolo"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=period + 1)
        
        if len(ohlcv) < period + 1:
            print(f"⚠️ No hay suficientes velas para calcular ATR")
            return None
        
        true_ranges = []
        for i in range(1, len(ohlcv)):
            high = ohlcv[i][2]
            low = ohlcv[i][3]
            prev_close = ohlcv[i-1][4]
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        atr = sum(true_ranges[-period:]) / period
        print(f"📊 ATR ({symbol}, {period} períodos): {atr:.4f}")
        return atr
        
    except Exception as e:
        print(f"❌ Error calculando ATR: {e}")
        return None

# ═════════════════════════════════════════════════════════════════════════════
# GESTIÓN DE RIESGO
# ═════════════════════════════════════════════════════════════════════════════
def get_risk_params(symbol, entry_price, atr):
    """Calcula Stop Loss y Take Profit basados en ATR"""
    # Extraer símbolo base (ej: ZEC de ZECUSDT.P)
    base_symbol = symbol.split('USDT')[0].replace('.', '')
    
    risk = RISK_CONFIG.get(base_symbol, RISK_CONFIG['default'])
    sl_mult = risk['sl_atr_mult']
    tp_rr = risk['tp_rr']
    
    print(f"⚙️ Riesgo para {base_symbol}: SL={sl_mult} ATR | TP RR=1:{tp_rr}")
    
    if atr:
        sl_distance = atr * sl_mult
        
        return {
            'LONG': {
                'stop_loss': round(entry_price - sl_distance, 4),
                'take_profit': round(entry_price + (sl_distance * tp_rr), 4),
                'sl_distance': sl_distance
            },
            'SHORT': {
                'stop_loss': round(entry_price + sl_distance, 4),
                'take_profit': round(entry_price - (sl_distance * tp_rr), 4),
                'sl_distance': sl_distance
            }
        }
    else:
        # Fallback: porcentaje fijo
        sl_pct = 0.02
        tp_pct = sl_pct * tp_rr
        
        return {
            'LONG': {
                'stop_loss': round(entry_price * (1 - sl_pct), 4),
                'take_profit': round(entry_price * (1 + tp_pct), 4)
            },
            'SHORT': {
                'stop_loss': round(entry_price * (1 + sl_pct), 4),
                'take_profit': round(entry_price * (1 - tp_pct), 4)
            }
        }

# ═════════════════════════════════════════════════════════════════════════════
# CALCULAR CANTIDAD CON % DEL BALANCE
# ═════════════════════════════════════════════════════════════════════════════
def calculate_quantity_with_percent(exchange, symbol, entry_price, available_balance, percent=POSITION_SIZE_PERCENT, leverage=LEVERAGE):
    """
    Calcula la cantidad de contratos basada en % del balance y apalancamiento
    """
    try:
        # Monto en USDT a usar
        usdt_to_use = available_balance * (percent / 100)
        
        # Con apalancamiento, el poder de compra aumenta
        buying_power = usdt_to_use * leverage
        
        # Cantidad de contratos
        quantity = buying_power / entry_price
        
        # Ajustar a precisión del mercado
        market = exchange.market(symbol)
        amount_precision = market['precision']['amount']
        
        # Redondear según la precisión
        if isinstance(amount_precision, int):
            quantity = round(quantity, amount_precision)
        else:
            # Si es string (ej: '0.001'), convertir a decimal
            precision = len(str(amount_precision).split('.')[-1]) if '.' in str(amount_precision) else 0
            quantity = round(quantity, precision)
        
        print(f"💰 Cálculo de posición:")
        print(f"   Balance disponible: {available_balance:.2f} USDT")
        print(f"   % a usar: {percent}%")
        print(f"   Monto: {usdt_to_use:.2f} USDT")
        print(f"   Apalancamiento: {leverage}x")
        print(f"   Poder de compra: {buying_power:.2f} USDT")
        print(f"   Cantidad: {quantity} contratos")
        
        return quantity
        
    except Exception as e:
        print(f"⚠️ Error calculando cantidad: {e}")
        return None

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURAR APALANCAMIENTO
# ═════════════════════════════════════════════════════════════════════════════
def set_leverage(exchange, symbol, leverage=LEVERAGE):
    """Configura el apalancamiento para el símbolo"""
    try:
        exchange.set_leverage(leverage, symbol)
        print(f"⚙️ Apalancamiento configurado: {leverage}x para {symbol}")
        return True
    except Exception as e:
        print(f"⚠️ No se pudo configurar apalancamiento: {e}")
        return False

# ═════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN DE ÓRDENES
# ═════════════════════════════════════════════════════════════════════════════
def execute_order(action, symbol, entry_price):
    """Ejecuta orden de mercado con gestión de riesgo"""
    exchange = None
    
    try:
        print(f"\n{'='*60}")
        print(f"🚀 EJECUTANDO ORDEN: {action} {symbol}")
        print(f"💲 Precio de entrada: {entry_price}")
        print(f"{'='*60}\n")
        
        # 1. Conectar con exchange
        exchange = get_exchange()
        
        # 2. Verificar balance
        available_balance = get_available_balance(exchange)
        if available_balance <= 0:
            print(f"❌ ERROR: Balance insuficiente o no disponible")
            return None
        
        # 3. Configurar apalancamiento
        set_leverage(exchange, symbol, LEVERAGE)
        
        # 4. Calcular ATR
        atr = calculate_atr(exchange, symbol)
        
        # 5. Calcular niveles de riesgo
        risk_params = get_risk_params(symbol, float(entry_price), atr)
        params = risk_params[action]
        
        print(f"\n📊 Niveles de riesgo:")
        print(f"   Stop Loss: {params['stop_loss']}")
        print(f"   Take Profit: {params['take_profit']}")
        
        # 6. Calcular cantidad con % del balance
        quantity = calculate_quantity_with_percent(
            exchange, symbol, float(entry_price), 
            available_balance, POSITION_SIZE_PERCENT, LEVERAGE
        )
        
        if not quantity or quantity <= 0:
            print(f"❌ ERROR: Cantidad calculada inválida")
            return None
        
        # 7. Ejecutar orden de mercado
        print(f"\n📤 Enviando orden de mercado...")
        
        if action == 'LONG':
            order = exchange.create_market_buy_order(symbol, quantity)
            print(f"✅ Orden LONG ejecutada:")
        else:
            order = exchange.create_market_sell_order(symbol, quantity)
            print(f"✅ Orden SHORT ejecutada:")
        
        print(f"   ID: {order.get('id', 'N/A')}")
        print(f"   Cantidad: {order.get('amount', quantity)}")
        print(f"   Precio: {order.get('average', entry_price)}")
        print(f"   Status: {order.get('status', 'N/A')}")
        
        # 8. Resumen final
        print(f"\n{'='*60}")
        print(f"✅ ORDEN COMPLETADA")
        print(f"   Acción: {action}")
        print(f"   Símbolo: {symbol}")
        print(f"   Entrada: {entry_price}")
        print(f"   SL: {params['stop_loss']}")
        print(f"   TP: {params['take_profit']}")
        print(f"   Cantidad: {quantity}")
        print(f"{'='*60}\n")
        
        return order
        
    except ccxt.InsufficientFunds as e:
        print(f"❌ Saldo insuficiente: {e}")
    except ccxt.NetworkError as e:
        print(f"❌ Error de red: {e}")
    except ccxt.ExchangeError as e:
        print(f"❌ Error de exchange: {e}")
    except Exception as e:
        print(f"❌ Error ejecutando orden: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if exchange:
            exchange.close()

# ═════════════════════════════════════════════════════════════════════════════
# PROCESAR ORDEN (THREAD)
# ═════════════════════════════════════════════════════════════════════════════
def process_order(action, symbol, price):
    """Procesa la orden en segundo plano"""
    try:
        if not BITUNIX_API_KEY or not BITUNIX_SECRET_KEY:
            print(f"❌ ERROR: API Keys no configuradas")
            return
        
        execute_order(action, symbol, float(price))
        
    except Exception as e:
        print(f"❌ Error en process_order: {e}")

# ═════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════
@app.route('/', methods=['GET'])
def index():
    return 'Bitunix Trading View Bot is running!'

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.get_data(as_text=True)
        print(f"\n{'='*50}")
        print(f"📨 WEBHOOK RECIBIDO: {message}")
        print(f"{'='*50}\n")
        
        parts = message.split('|')
        
        if len(parts) >= 3:
            action = parts[0].strip().upper()
            symbol = parts[1].strip()
            price = parts[2].strip()
            
            if action not in ['LONG', 'SHORT']:
                print(f"⚠️ Acción no válida: {action}")
                return jsonify({'status': 'error', 'message': 'Invalid action'}), 400
            
            thread = threading.Thread(target=process_order, args=(action, symbol, price))
            thread.start()
            
            return jsonify({
                'status': 'success',
                'message': f'{action} order for {symbol} processing'
            }), 200
        else:
            return jsonify({'status': 'error', 'message': 'Invalid format'}), 400
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ═════════════════════════════════════════════════════════════════════════════
# INICIO
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
