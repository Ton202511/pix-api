# app.py
import os
import logging
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# --- Config from environment ---
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()       # Production token (APP_USR-...)
MP_API_URL = os.getenv("MP_API_URL", "https://api.mercadopago.com").rstrip("/")
ESP_BASE = os.getenv("ESP_BASE", "").strip()                    # e.g. "http://192.168.0.50:80" (must be reachable by Render -> local networks not reachable)
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play").strip()     # e.g. "/play"
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "").strip()        # optional
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 6))          # seconds
NOTIFY_RETRY = int(os.getenv("NOTIFY_RETRY", 2))                # how many attempts to notify ESP

# --- Helper: notify ESP (robust) ---
def notify_esp():
    """
    Notify the ESP device to play audio.
    Returns (ok: bool, info: str)
    """
    if not ESP_BASE:
        logging.error("notify_esp: ESP_BASE not configured.")
        return False, "ESP_BASE_missing"

    # construct URL safely
    base = ESP_BASE.rstrip("/")
    path = ESP_PLAY_PATH.lstrip("/")
    url = f"{base}/{path}" if path else base

    headers = {}
    if ESP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"

    last_err = None
    for attempt in range(1, NOTIFY_RETRY + 1):
        try:
            logging.info("notify_esp: attempt %d -> %s", attempt, url)
            # prefer GET for simple ESP endpoints, but try both GET then POST fallback
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            logging.info("notify_esp: GET status=%s body=%s", r.status_code, (r.text or "")[:300])
            if r.ok:
                return True, f"GET {r.status_code}"
            # try POST as fallback
            r2 = requests.post(url, headers=headers, timeout=REQUEST_TIMEOUT)
            logging.info("notify_esp: POST status=%s body=%s", r2.status_code, (r2.text or "")[:300])
            if r2.ok:
                return True, f"POST {r2.status_code}"
            last_err = f"GET {r.status_code}, POST {r2.status_code}"
        except Exception as e:
            logging.exception("notify_esp: exception")
            last_err = str(e)
    return False, last_err or "unknown_error"

# --- Helper: fetch payment details from Mercado Pago ---
def fetch_payment_details(payment_id):
    """
    Call Mercado Pago /v1/payments/{payment_id} and return (json, None) on success
    or (None, error_str) on failure.
    """
    if not MP_ACCESS_TOKEN:
        logging.error("fetch_payment_details: MP_ACCESS_TOKEN not configured.")
        return None, "MP_ACCESS_TOKEN_missing"

    url = f"{MP_API_URL}/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    try:
        logging.info("fetch_payment_details: GET %s", url)
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.exception("fetch_payment_details: request failed")
        return None, str(e)

    if r.status_code != 200:
        logging.error("fetch_payment_details: MP returned %s %s", r.status_code, r.text[:400])
        return None, f"mp_status_{r.status_code}:{r.text}"
    try:
        return r.json(), None
    except Exception as e:
        logging.exception("fetch_payment_details: invalid json")
        return None, "invalid_json"

# --- Endpoint: home & health ---
@app.route("/", methods=["GET"])
def home():
    return "API do Pix rodando com sucesso!", 200

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200

# --- Endpoint: create a payment (for tests or integration) ---
@app.route("/create_payment", methods=["POST"])
def create_payment():
    """
    Creates a payment using Mercado Pago API.
    Expects JSON body with at least:
      - transaction_amount (number)
      - payment_method_id (e.g. "pix")
      - payer (object with email)
    Optional:
      - description, notification_url, external_reference, additional_info, etc.
    Returns Mercado Pago response (proxy).
    """
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"ok": False, "reason": "no_json"}), 400

    if not MP_ACCESS_TOKEN:
        return jsonify({"ok": False, "reason": "mp_token_missing"}), 500

    url = f"{MP_API_URL}/v1/payments"
    headers = {
        "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    # optional idempotency key if client provided
    idempotency_key = request.headers.get("X-Idempotency-Key") or payload.get("idempotency_key")
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key

    try:
        logging.info("create_payment: forwarding to MP %s body_keys=%s", url, list(payload.keys()))
        r = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logging.exception("create_payment: request failed")
        return jsonify({"ok": False, "reason": "mp_request_failed", "detail": str(e)}), 502

    # pass-through status and body
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    return jsonify({"ok": r.ok, "status_code": r.status_code, "mp_response": body}), r.status_code

# --- Endpoint: webhook for Mercado Pago ---
@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Mercado Pago will POST here.
    Expected payload examples:
      { "action":"payment.updated", "api_version":"v1", "data": {"id": "12345"}, ... }
      or resource / resource.id depending on event.
    Flow:
      1) extract payment id
      2) fetch payment details from MP
      3) if payment is PIX and approved -> notify ESP
    """
    payload = request.get_json(silent=True) or {}
    logging.info("Webhook payload: %s", payload)

    # try multiple extraction patterns
    payment_id = None
    try:
        if isinstance(payload.get("data"), dict):
            payment_id = payload["data"].get("id") or payload["data"].get("id_payment")
        if not payment_id and isinstance(payload.get("resource"), dict):
            payment_id = payload["resource"].get("id")
        if not payment_id:
            payment_id = payload.get("id") or payload.get("data_id") or (request.args.get("data.id") or request.args.get("id"))
    except Exception:
        logging.exception("error extracting id")

    if not payment_id:
        logging.warning("webhook: no payment id found in payload")
        return jsonify({"ok": False, "reason": "no_payment_id"}), 400

    logging.info("Payment ID detected: %s", payment_id)

    # fetch payment from Mercado Pago
    payment, err = fetch_payment_details(payment_id)
    if err:
        logging.error("MP fetch failed: %s", err)
        # 502 to tell MP to retry delivery later
        return jsonify({"ok": False, "reason": "mp_fetch_failed", "detail": err}), 502

    # pick status and method safely
    status = (payment.get("status") or "").lower()
    payment_method = (payment.get("payment_method_id") or payment.get("payment_type_id") or "").lower()
    payment_type = (payment.get("payment_type") or "").lower()

    logging.info("Payment fetched: id=%s status=%s method=%s type=%s", payment.get("id"), status, payment_method, payment_type)

    # Determine PIX & approval
    is_pix = ("pix" in payment_method) or ("pix" in payment_type)
    is_approved = status in ("approved", "paid", "paid_off")

    if not is_approved:
        logging.info("Payment not approved (status=%s) - nothing to do", status)
        # return 200 to stop retries (MP already got it)
        return jsonify({"ok": True, "note": "not_approved", "status": status}), 200

    # optional: require pix specifically; comment out to accept any approved payment
    if not is_pix:
        logging.info("Payment approved but not PIX (method=%s) - ignoring", payment_method)
        return jsonify({"ok": True, "note": "not_pix", "payment_method": payment_method}), 200

    # finally: notify ESP
    ok, info = notify_esp()
    if not ok:
        logging.error("notify_esp failed: %s", info)
        # return 502 so MP can retry webhook later
        return jsonify({"ok": False, "reason": "esp_notify_failed", "detail": info}), 502

    logging.info("ESP notified successfully for payment %s", payment_id)
    return jsonify({"ok": True, "payment_id": payment_id}), 200

# --- Run (dev) ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
