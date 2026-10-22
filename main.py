from flask import Flask, request, jsonify
import os

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Bitunix TradingView Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.data.decode("utf-8")
    print("Señal recibida:", data)

    # Aquí más adelante conectaremos Bitunix
    # Por ahora solo recibimos y mostramos la señal

    return jsonify({"status": "ok", "message": "Signal received"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
