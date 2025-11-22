# app.py
import os
import time
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, List
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# -------------------------
# Config via environment
# -------------------------
ESP_BASE = os.getenv("ESP_BASE", "http://192.168.0.58:80")
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play")
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "")        # opcional (Bearer)
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")      # obrigatório para buscar MP
NOTIFY_RETRY = int(os.getenv("NOTIFY_RETRY", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))  # segundos
MP_SEARCH_URL = os.getenv(
    "MP_SEARCH_URL",
    "https://api.mercadopago.com/v1/payments/search?sort=date_created&criteria=desc&limit=10"
)

# monitor / ingest secrets
MONITOR_SECRET = os.getenv("MONITOR_SECRET", "base123")  # X-Auth esperado dos ESPs

# persistence file for processed IDs
PROCESSED_STORE = os.getenv("PROCESSED_STORE", "processed_ids.json")

# -------------------------
# Runtime state
# -------------------------
processed_ids = set()
processed_lock = threading.Lock()

# store last_seen / events / logs for devices (in-memory)
last_seen: Dict[str, Dict] = {}
events: Dict[str, List[Dict]] = {}
logs: Dict[str, List[Dict]] = {}
debug_state: Dict[str, bool] = {}

# -------------------------
# Helpers
# -------------------------
def load_processed():
    global processed_ids
    try:
        if os.path.exists(PROCESSED_STORE):
            with open(PROCESSED_STORE, "r", encoding="utf-8") as f:
                arr = json.load(f)
                processed_ids = set(arr)
                app.logger.info(f"Loaded {len(processed_ids)} processed ids from {PROCESSED_STORE}")
    except Exception as e:
        app.logger.warning("Could not load processed ids: %s" % e)

def save_processed():
    try:
        with processed_lock:
            with open(PROCESSED_STORE, "w", encoding="utf-8") as f:
                json.dump(list(processed_ids), f)
    except Exception as e:
        app.logger.warning("Could not save processed ids: %s" % e)

def auth_ok(req):
    """Checa X-Auth header para rotas de ingest do ESP"""
    return req.headers.get("X-Auth") == MONITOR_SECRET

def notify_esp_play(payment_id: str) -> bool:
    """Notifica o ESP: pode ser GET ou POST dependendo do ESP; respeita ESP_AUTH_TOKEN se informado."""
    notify_url = f"{ESP_BASE}{ESP_PLAY_PATH}"
    headers = {}
    if ESP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"
    # opcionalmente mandar payload com id
    payload = {"payment_id": payment_id}
    for attempt in range(1, NOTIFY_RETRY + 1):
        try:
            app.logger.info(f"notify_esp: tentativa {attempt} -> {notify_url}")
            # usar POST por padrão (mais flexível). Se seu ESP espera GET, ajuste aqui.
            r = requests.post(notify_url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT, verify=True)
            app.logger.info(f"Resposta ESP: {r.status_code}")
            if r.status_code in (200, 204):
                return True
        except requests.RequestException as e:
            app.logger.warning(f"Erro ao notificar ESP (tentativa {attempt}): {e}")
            time.sleep(1)
    return False

# -------------------------
# Flask endpoints (monitor ingest)
# -------------------------
@app.route("/")
def index():
    return jsonify({"ok": True, "msg": "Servidor Flask ativo e monitorando Pix recebidos!"}), 200

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    dev = data.get("device_id")
    if not dev:
        return jsonify({"error": "missing device_id"}), 400
    last_seen[dev] = {
        "ts": datetime.utcnow().isoformat(),
        "ip": data.get("ip"),
        "rssi": data.get("rssi"),
        "uptime_ms": data.get("uptime_ms"),
        "debug": data.get("debug"),
        "last_pix_id": data.get("last_pix_id"),
    }
    return jsonify({"ok": True}), 200

@app.route("/event", methods=["POST"])
def event():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    dev = data.get("device_id")
    if not dev:
        return jsonify({"error": "missing device_id"}), 400
    evt = {
        "ts": datetime.utcnow().isoformat(),
        "type": data.get("type"),
        "payment_id": data.get("payment_id"),
        "raw": data
    }
    events.setdefault(dev, []).append(evt)
    app.logger.info(f"Event from {dev}: {evt}")
    return jsonify({"ok": True}), 200

@app.route("/log", methods=["POST"])
def log():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    dev = data.get("device_id")
    if not dev:
        return jsonify({"error": "missing device_id"}), 400
    lg = {
        "ts": datetime.utcnow().isoformat(),
        "type": data.get("type"),
        "message": data.get("message"),
        "raw": data
    }
    logs.setdefault(dev, []).append(lg)
    app.logger.debug(f"Log from {dev}: {lg}")
    return jsonify({"ok": True}), 200

@app.route("/debug", methods=["GET", "POST"])
def debug_route():
    if not auth_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    if request.method == "GET":
        dev = request.args.get("device_id")
        if not dev:
            return jsonify({"error": "missing device_id"}), 400
        return jsonify({"debug": bool(debug_state.get(dev, False))}), 200
    else:
        data = request.get_json(silent=True) or {}
        dev = data.get("device_id")
        dbg = data.get("debug")
        if dev is None or dbg is None:
            return jsonify({"error": "missing"}), 400
        debug_state[dev] = bool(dbg)
        return jsonify({"ok": True, "debug": debug_state[dev]}), 200

@app.route("/status", methods=["GET"])
def status():
    out = []
    now = datetime.utcnow()
    for dev, info in last_seen.items():
        try:
            ts = datetime.fromisoformat(info["ts"])
        except Exception:
            ts = now
        online = (now - ts) < timedelta(minutes=3)
        out.append({
            "device_id": dev,
            "last_seen": info["ts"],
            "online": online,
            "ip": info.get("ip"),
            "rssi": info.get("rssi"),
            "uptime_ms": info.get("uptime_ms"),
            "debug": info.get("debug"),
            "last_pix_id": info.get("last_pix_id"),
            "events_count": len(events.get(dev, [])),
            "logs_count": len(logs.get(dev, [])),
        })
    out_sorted = sorted(out, key=lambda x: x["device_id"])
    return jsonify(out_sorted), 200

@app.route("/status/<device_id>", methods=["GET"])
def status_device(device_id):
    info = last_seen.get(device_id)
    if not info:
        return jsonify({"error": "not found"}), 404
    try:
        ts = datetime.fromisoformat(info["ts"])
    except Exception:
        ts = datetime.utcnow()
    online = (datetime.utcnow() - ts) < timedelta(minutes=3)
    return jsonify({
        "device_id": device_id,
        "last_seen": info["ts"],
        "online": online,
        "ip": info.get("ip"),
        "rssi": info.get("rssi"),
        "uptime_ms": info.get("uptime_ms"),
        "debug": info.get("debug"),
        "last_pix_id": info.get("last_pix_id"),
        "events": events.get(device_id, []),
        "logs": logs.get(device_id, []),
    }), 200

# -------------------------
# MercadoPago polling
# -------------------------
def buscar_pagamentos_once():
    """Busca pagamentos recentes e processa novos PIX aprovados."""
    if not MP_ACCESS_TOKEN:
        app.logger.warning("MP_ACCESS_TOKEN não configurado. Pulei busca.")
        return

    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    try:
        r = requests.get(MP_SEARCH_URL, headers=headers, timeout=REQUEST_TIMEOUT, verify=True)
        r.raise_for_status()
    except requests.RequestException as e:
        app.logger.warning(f"Erro ao buscar pagamentos MP: {e}")
        return

    try:
        body = r.json()
    except Exception as e:
        app.logger.warning(f"Resposta MP não é JSON: {e}")
        return

    results = body.get("results") or []
    if not isinstance(results, list):
        app.logger.debug("MP: results não é lista")
        return

    for pagamento in results:
        payment_id = pagamento.get("id")
        if not payment_id:
            continue

        # normaliza para string
        payment_id = str(payment_id)

        with processed_lock:
            if payment_id in processed_ids:
                continue

        status = pagamento.get("status")
        method = pagamento.get("payment_method_id")
        # Detecta Pix aprovado
        if status == "approved" and method == "pix":
            valor = pagamento.get("transaction_amount", pagamento.get("total_paid_amount"))
            app.logger.info(f"Pix recebido: R${valor} | ID: {payment_id}")
            # marca como processado (antes de notificar para evitar duplicatas em caso de retry)
            with processed_lock:
                processed_ids.add(payment_id)
            save_processed()

            # notifica ESP
            ok = notify_esp_play(payment_id)
            if ok:
                app.logger.info(f"ESP notificado com sucesso para {payment_id}")
            else:
                app.logger.warning(f"Falha ao notificar ESP para {payment_id}")

# loop de monitoramento em thread separada
def monitor_loop():
    while True:
        try:
            buscar_pagamentos_once()
        except Exception as e:
            app.logger.exception("Erro no loop de monitoramento: %s" % e)
        time.sleep(CHECK_INTERVAL)

# -------------------------
# Inicialização
# -------------------------
if __name__ == "__main__":
    load_processed()
    # start background thread
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
