from flask import Flask, request, jsonify
import os
import time
import hashlib
import uuid
import json
import requests

app = Flask(__name__)

# ====== CONFIGURACIÓN ======
API_KEY = os.environ.get("BITUNIX_API_KEY", "")
API_SECRET = os.environ.get("BITUNIX_API_SECRET", "")
BASE_URL = "https://fapi.bitunix.com"
TRADE_QTY = os.environ.get("TRADE_QTY", "0.01")   # Cantidad fija para pruebas

def generate_signature(nonce, timestamp, body=""):
    """Genera la firma double SHA256 de Bitunix"""
    digest_input = f"{nonce}{timestamp}{API_KEY}{body}"
    first_hash = hashlib.sha256(digest_input.encode()).hexdigest()
    signature = hashlib.sha256((first_hash + API_SECRET).encode()).hexdigest()
    return signature

def place_order(symbol, side, trade_side, qty):
    """Coloca una orden de mercado en Bitunix Futures"""
    endpoint = "/api/v1/futures/trade/place_order"
    url = BASE_URL + endpoint

    # Limpiar símbolo (quitar .P si viene de TradingView)
    symbol = symbol.replace(".P", "").replace("USDT.P", "USDT")

    order_data = {
        "symbol": symbol,
        "side": side,              # BUY o SELL
        "tradeSide": trade_side,   # OPEN o CLOSE
        "orderType": "MARKET",
        "qty": str(qty)
    }

    body = json.dumps(order_data, separators=(',', ':'), sort_keys=True)
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))
    sign = generate_signature(nonce, timestamp, body)

    headers = {
        "api-key": API_KEY,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "language": "en-US",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, data=body, timeout=10)
        result = response.json()
        print(f"Respuesta Bitunix: {result}")
        return result
    except Exception as e:
        print(f"Error al enviar orden: {e}")
        return {"error": str(e)}

@app.route("/", methods=["GET"])
def home():
    return "Bitunix TradingView Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.data.decode("utf-8").strip()
    print(f"Señal recibida: {data}")

    if not API_KEY or not API_SECRET:
        print("ERROR: Faltan API keys")
        return jsonify({"status": "error", "message": "API keys missing"}), 500

    try:
        parts = data.split("|")
        action = parts[0].upper()          # LONG, SHORT, CLOSE_LONG, CLOSE_SHORT
        symbol = parts[1] if len(parts) > 1 else ""
        price = parts[2] if len(parts) > 2 else ""

        print(f"Acción: {action} | Símbolo: {symbol} | Precio: {price}")

        if action == "LONG":
            result = place_order(symbol, "BUY", "OPEN", TRADE_QTY)
        elif action == "SHORT":
            result = place_order(symbol, "SELL", "OPEN", TRADE_QTY)
        elif action == "CLOSE_LONG":
            result = place_order(symbol, "SELL", "CLOSE", TRADE_QTY)
        elif action == "CLOSE_SHORT":
            result = place_order(symbol, "BUY", "CLOSE", TRADE_QTY)
        else:
            print(f"Acción desconocida: {action}")
            return jsonify({"status": "ignored"}), 200

        return jsonify({"status": "ok", "result": result}), 200

    except Exception as e:
        print(f"Error procesando señal: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
