from flask import Flask, request, jsonify
import threading
import ccxt
import os

app = Flask(__name__)

# Bitunix configuration
exchange = ccxt.bitunix({
    'apiKey': os.environ.get('BITUNIX_API_KEY'),
    'secret': os.environ.get('BITUNIX_SECRET_KEY'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
    }
})

def process_order(action, symbol, price):
    """Process order in background"""
    try:
        print(f"Processing {action} order for {symbol} at {price}")
        
        # Place market order
        if action == 'LONG':
            order = exchange.create_market_buy_order(symbol, 0.001)  # Adjust quantity
            print(f"Order placed: {order}")
        elif action == 'SHORT':
            order = exchange.create_market_sell_order(symbol, 0.001)
            print(f"Order placed: {order}")
            
    except Exception as e:
        print(f"Error processing order: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    """Receive webhook from TradingView"""
    try:
        # Get the message
        message = request.get_data(as_text=True)
        print(f"Received webhook: {message}")
        
        # Parse message (format: ACTION|SYMBOL|PRICE)
        parts = message.split('|')
        if len(parts) >= 3:
            action = parts[0]  # LONG or SHORT
            symbol = parts[1]  # ARBUSDT.P, etc.
            price = float(parts[2])
            
            # Process order in background thread (non-blocking)
            thread = threading.Thread(target=process_order, args=(action, symbol, price))
            thread.start()
            
            # IMMEDIATELY respond to TradingView
            return jsonify({'status': 'success', 'message': 'Order processing'}), 200
        
        return jsonify({'status': 'error', 'message': 'Invalid format'}), 400
        
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check for UptimeRobot"""
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
