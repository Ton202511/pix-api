from flask import Flask
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
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))  # segundos

# 🧠 Armazena IDs já processados para evitar duplicação
processed_ids = set()

def buscar_pagamentos():
    url = "https://api.mercadopago.com/v1/payments/search?sort=date_created&criteria=desc&limit=10"
    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        pagamentos = response.json().get("results", [])
    except Exception as e:
        print("Erro ao buscar pagamentos:", e)
        return

    for pagamento in pagamentos:
        payment_id = pagamento.get("id")
        if not payment_id or payment_id in processed_ids:
            continue

        if pagamento.get("status") == "approved" and pagamento.get("payment_method_id") == "pix":
            valor = pagamento.get("transaction_amount")
            print(f"💰 Pix recebido: R${valor} | ID: {payment_id}")
            processed_ids.add(payment_id)

            # 🔔 Notifica ESP32
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
                        print("✅ ESP notificado com sucesso.")
                        break
                except Exception as e:
                    print(f"Erro ao notificar ESP (tentativa {attempt}):", e)
                    time.sleep(1)

@app.route('/')
def index():
    return 'Servidor Flask ativo e monitorando Pix recebidos!', 200

# 🚀 Loop de monitoramento contínuo
def iniciar_monitoramento():
    while True:
        buscar_pagamentos()
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    from threading import Thread
    Thread(target=iniciar_monitoramento).start()
    app.run(host="0.0.0.0", port=port)
