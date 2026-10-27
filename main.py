from flask import Flask, request, jsonify
import threading
import os

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE BITUNIX (Se cargará desde variables de entorno)
# ══════════════════════════════════════════════════════════════════════════════
BITUNIX_API_KEY = os.environ.get('BITUNIX_API_KEY', '')
BITUNIX_SECRET_KEY = os.environ.get('BITUNIX_SECRET_KEY', '')

# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PARA PROCESAR LA ORDEN (Se ejecuta en segundo plano)
# ══════════════════════════════════════════════════════════════════════════════
def process_order(action, symbol, price):
    """
    Procesa la orden en segundo plano.
    Aquí irá la lógica de conexión con Bitunix más adelante.
    Por ahora solo registra la orden recibida.
    """
    print(f"📥 Orden recibida: {action} | {symbol} | Precio: {price}")
    print(f"🔑 API Key configurada: {'Sí' if BITUNIX_API_KEY else 'No'}")
    
    # TODO: Aquí conectaremos con Bitunix en el siguiente paso
    # Por ahora solo logueamos para verificar que funciona

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/', methods=['GET'])
def index():
    """Página principal - verifica que el bot está corriendo"""
    return 'Bitunix Trading View Bot is running!'

@app.route('/health', methods=['GET'])
def health():
    """Endpoint para UptimeRobot - mantiene el bot despierto"""
    return jsonify({'status': 'ok'}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Recibe las alertas de TradingView.
    Formato esperado: LONG|SYMBOL|PRICE  o  SHORT|SYMBOL|PRICE
    """
    try:
        # Obtener el mensaje crudo de TradingView
        message = request.get_data(as_text=True)
        print(f"\n{'='*50}")
        print(f"📨 WEBHOOK RECIBIDO: {message}")
        print(f"{'='*50}\n")
        
        # Parsear el mensaje (formato: ACCION|SIMBOLO|PRECIO)
        parts = message.split('|')
        
        if len(parts) >= 3:
            action = parts[0].strip().upper()   # LONG o SHORT
            symbol = parts[1].strip()           # Ej: ARBUSDT.P
            price = parts[2].strip()            # Precio
            
            # Validar que la acción sea correcta
            if action not in ['LONG', 'SHORT']:
                print(f"⚠️ Acción no válida: {action}")
                return jsonify({'status': 'error', 'message': 'Invalid action'}), 400
            
            # Procesar la orden en un hilo separado (no bloquea la respuesta)
            thread = threading.Thread(target=process_order, args=(action, symbol, price))
            thread.start()
            
            # Responder INMEDIATAMENTE a TradingView (evita timeout)
            return jsonify({
                'status': 'success', 
                'message': f'{action} order for {symbol} processing'
            }), 200
        else:
            print(f"⚠️ Formato incorrecto: {message}")
            return jsonify({'status': 'error', 'message': 'Invalid format'}), 400
            
    except Exception as e:
        print(f"❌ Error en webhook: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ═════════════════════════════════════════════════════════════════════════════
# INICIO DEL SERVIDOR
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
