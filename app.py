import os
import logging
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")  # seu token (colocado como var de ambiente no Render)
ESP_BASE = os.getenv("ESP_BASE", "")            # ex: http://192.168.0.50:80 (pode ficar vazio em prod)
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play")
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "")  # opcional

MP_API_URL = os.getenv("MP_API_URL", "https://api.mercadopago.com")

def notify_esp():
    """Envia comando ao ESP32 para tocar o áudio."""
    if not ESP_BASE:
        logging.error("ESP_BASE não configurado, impossivel notificar ESP.")
        return False, "ESP_BASE missing"

    url = ESP_BASE.rstrip("/") + ESP_PLAY_PATH
    headers = {}
    if ESP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"
    try:
        r = requests.post(url, headers=headers, timeout=6)
        logging.info("Notified ESP: %s -> status %s", url, r.status_code)
        return (r.ok, r.text if not r.ok else "OK")
    except Exception as e:
        logging.exception("Erro ao notificar ESP: %s", e)
        return False, str(e)

def fetch_payment(payment_id):
    """Consulta o Mercado Pago para confirmar status do pagamento."""
    if not MP_ACCESS_TOKEN:
        logging.error("MP_ACCESS_TOKEN não configurado.")
        return None
    url = f"{MP_API_URL}/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
    except Exception as e:
        logging.exception("Erro ao chamar MP: %s", e)
        return None
    if r.status_code != 200:
        logging.error("MP fetch failed %s: %s", r.status_code, r.text)
        return None
    return r.json()

@app.route("/")
def index():
    return "API do Pix rodando com sucesso!"

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Recebe notificação do Mercado Pago.
    Fluxo:
    1) tenta extrair payment_id do payload (vários formatos possíveis)
    2) consulta /v1/payments/{id} para confirmar status e método
    3) se for PIX e aprovado -> notifica ESP32
    """
    payload = request.get_json(silent=True) or {}
    logging.info("Webhook payload: %s", payload)

    # extrair possíveis IDs (vários formatos)
    payment_id = None
    try:
        if isinstance(payload.get("data"), dict) and payload["data"].get("id"):
            payment_id = payload["data"]["id"]
        elif payload.get("id"):
            payment_id = payload.get("id")
        elif isinstance(payload.get("resource"), dict) and payload["resource"].get("id"):
            payment_id = payload["resource"]["id"]
        elif request.args.get("id"):
            payment_id = request.args.get("id")
    except Exception:
        logging.exception("Erro ao extrair payment id do payload")

    if not payment_id:
        logging.warning("Payment ID não encontrado no payload.")
        return jsonify({"ok": False, "reason": "no_payment_id"}), 400

    logging.info("Payment ID detectado: %s", payment_id)

    # confirmar com Mercado Pago
    pay = fetch_payment(payment_id)
    if not pay:
        return jsonify({"ok": False, "reason": "mp_fetch_failed"}), 502

    status = pay.get("status") or pay.get("transaction_details", {}).get("status")
    payment_method = pay.get("payment_method_id") or pay.get("payment_type_id") or ""
    logging.info("Payment status=%s method=%s", status, payment_method)

    is_pix = "pix" in str(payment_method).lower()
    is_approved = str(status).lower() in ("approved", "paid")

    if is_approved and is_pix:
        ok, info = notify_esp()
        if ok:
            return jsonify({"ok": True, "action": "played"}), 200
        else:
            return jsonify({"ok": False, "reason": "esp_failed", "info": info}), 502

    logging.info("Pagamento não qualificado (status=%s method=%s).", status, payment_method)
    return jsonify({"ok": True, "note": "payment_not_qualifying", "status": status}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
