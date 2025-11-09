from flask import Flask, request
import requests
import os
import time

app = Flask(__name__)

# 🔧 Configurações via variáveis de ambiente
ESP_BASE = os.getenv("ESP_BASE", "http://192.168.0.58:80")
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play")
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "")
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
NOTIFY_RETRY = int(os.getenv("NOTIFY_RETRY", 2))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 6))

@app.route('/')
def index():
    return 'Servidor Flask ativo e pronto para receber webhooks!', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook payload:", data)

    # 🔍 Extrai o payment_id
    payment_id = None
    if isinstance(data, dict):
        payment_id = data.get("data", {}).get("id")

    if not payment_id:
        print("payment_id não encontrado no payload.")
        return '', 400

    print(f"Payment ID detectado: {payment_id}")

    # 🔎 Consulta os detalhes do pagamento no Mercado Pago
    payment_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    mp_headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}"
    }

    try:
        response = requests.get(payment_url, headers=mp_headers, timeout=REQUEST_TIMEOUT)
        payment_data = response.json()
        print("Dados do pagamento:", payment_data)
    except Exception as e:
        print("Erro ao consultar pagamento:", e)
        return '', 500

    # ✅ Verifica se é Pix aprovado
    if payment_data.get("status") == "approved" and payment_data.get("payment_method_id") == "pix":
        print("Pagamento Pix aprovado. Tentando notificar ESP32...")

        notify_url = f"{ESP_BASE}{ESP_PLAY_PATH}"
        esp_headers = {}
        if ESP_AUTH_TOKEN:
            esp_headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"

        for attempt in range(1, NOTIFY_RETRY + 1):
            try:
                print(f"notify_esp: tentativa {attempt} -> {notify_url}")
                r = requests.get(notify_url, headers=esp_headers, timeout=REQUEST_TIMEOUT)
                print("Resposta ESP:", r.status_code)
                if r.status_code == 200:
                    print("ESP notificado com sucesso.")
                    break
            except Exception as e:
                print(f"Erro ao notificar ESP (tentativa {attempt}):", e)
                time.sleep(1)

    return '', 200

# 🚀 Configura a porta dinâmica para o Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
