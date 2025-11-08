# app.py
import os
import logging
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Config via env
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
MP_API_URL = os.getenv("MP_API_URL", "https://api.mercadopago.com")  # geralmente não precisa mudar
ESP_BASE = os.getenv("ESP_BASE", "").strip()  # ex: "http://192.168.0.123:80"
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play").strip()  # ex: "/play"
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "").strip()  # opcional

REQUEST_TIMEOUT = 6  # segundos para chamadas HTTP externas

@app.route("/")
def home():
    return "API do Pix rodando com sucesso!", 200

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200

def notify_esp():
    """Envia requisição para o ESP tocar o áudio."""
    if not ESP_BASE:
        logging.error("ESP_BASE não configurado; impossível notificar ESP.")
        return False, "ESP_BASE missing"

    url = ESP_BASE.rstrip("/") + "/" + ESP_PLAY_PATH.lstrip("/")
    headers = {}
    if ESP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"

    try:
        logging.info("Notificando ESP -> %s", url)
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        logging.info("Resposta ESP: %s %s", r.status_code, r.text[:200])
        return True, r.status_code
    except Exception as e:
        logging.exception("Erro ao notificar ESP: %s", e)
        return False, str(e)

def fetch_payment_details(payment_id):
    """Busca o pagamento na API do Mercado Pago para confirmar status e método de pagamento."""
    if not MP_ACCESS_TOKEN:
        logging.error("MP_ACCESS_TOKEN não configurado; não é possível buscar pagamento.")
        return None, "MP_ACCESS_TOKEN missing"

    url = f"{MP_API_URL.rstrip('/')}/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    try:
        logging.info("Buscando pagamento MP id=%s", payment_id)
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            logging.warning("MP API retornou %s: %s", r.status_code, r.text)
            return None, f"mp_status_{r.status_code}"
        return r.json(), None
    except Exception as e:
        logging.exception("Erro ao chamar MP API: %s", e)
        return None, str(e)

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Endpoint configurado como webhook no painel do Mercado Pago.
    O Mercado Pago envia um JSON como:
    {
      "action": "payment.updated",
      "api_version": "v1",
      "data": {"id": "123456"},
      ...
    }
    """
    try:
        data = request.get_json(force=True, silent=True)
        logging.info("Webhook recebido: %s", data)

        if not data:
            logging.warning("Webhook vazio ou não JSON")
            return jsonify({"ok": False, "reason": "no_json"}), 400

        # Extrair payment id (padrão: data.id)
        payment_id = None
        if isinstance(data.get("data"), dict):
            payment_id = data["data"].get("id") or data["data"].get("id_payment") or data["data"].get("payment_id")
        # fallback: procurar em raíz
        if not payment_id:
            payment_id = data.get("id") or data.get("data_id")

        if not payment_id:
            logging.warning("nenhum payment id no webhook: %s", data)
            return jsonify({"ok": False, "reason": "no_payment_id"}), 400

        # Buscar detalhes do pagamento na API do Mercado Pago para confirmar
        payment, err = fetch_payment_details(payment_id)
        if err:
            # se não conseguimos buscar, retornamos 502 para MP (assim ela pode tentar novamente)
            return jsonify({"ok": False, "reason": "mp_fetch_error", "detail": err}), 502

        logging.info("Detalhes do pagamento: id=%s status=%s", payment.get("id"), payment.get("status"))

        # Confirme que é pagamento via PIX e que está aprovado
        status = (payment.get("status") or "").lower()
        payment_method = (payment.get("payment_method_id") or "").lower()
        # alguns campos alternativos:
        # payment_type = payment.get("type")  # nem sempre presente
        is_pix = ("pix" in payment_method) or ("pix" in (payment.get("payment_type", "") or "").lower())

        if status not in ("approved", "paid", "paid_off"):  # approved é o comum
            logging.info("Pagamento não aprovado (status=%s) — não acionar ESP.", status)
            return jsonify({"ok": False, "reason": "not_approved", "status": status}), 200

        # opcional: exigir que seja PIX (se quiser)
        # se quiser aceitar qualquer pagamento aprovado, comente a verificação abaixo
        if not is_pix:
            logging.info("Pagamento aprovado mas não identificado como PIX (payment_method=%s).", payment_method)
            # Se não quiser filtrar por PIX, mude esse return para acionar o ESP mesmo assim.
            return jsonify({"ok": False, "reason": "not_pix", "payment_method": payment_method}), 200

        # Se chegou aqui: é PIX e aprovado => notificar ESP
        ok, info = notify_esp()
        if not ok:
            return jsonify({"ok": False, "reason": "esp_notify_failed", "info": info}), 502

        return jsonify({"ok": True, "payment_id": payment_id}), 200

    except Exception as e:
        logging.exception("Erro ao processar webhook: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # porta definida pelo Render (env var PORT) em produção; para dev local use 5000
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
