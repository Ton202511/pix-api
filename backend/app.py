import os, time, json, threading, hashlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file
from gtts import gTTS
from pydub import AudioSegment   # <-- biblioteca para converter MP3 → WAV
import requests

app = Flask(__name__)
AUDIO_DIR = "audios"; os.makedirs(AUDIO_DIR, exist_ok=True)
PROCESSED_STORE = "processed_ids.json"
processed_ids = set(); processed_lock = threading.Lock()

# ENV config
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_SEARCH_URL = os.getenv("MP_SEARCH_URL", "https://api.mercadopago.com/v1/payments/search?sort=date_created&criteria=desc&limit=10")
ESP_BASE = os.getenv("ESP_BASE", "http://192.168.0.58:80")
ESP_AUTH_TOKEN = os.getenv("ESP_AUTH_TOKEN", "")
ESP_PLAY_PATH = os.getenv("ESP_PLAY_PATH", "/play")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "6"))

# Helpers
def frase_pix(nome, valor): return f"PIX recebido de {nome}, valor {valor}."
def make_id(nome, valor): return hashlib.sha1(f"{nome}|{valor}".encode()).hexdigest()[:8]
def wav_path(audio_id): return os.path.join(AUDIO_DIR, f"{audio_id}.wav")

def load_processed():
    global processed_ids
    if os.path.exists(PROCESSED_STORE):
        with open(PROCESSED_STORE, "r", encoding="utf-8") as f:
            processed_ids.update(json.load(f))

def save_processed():
    with processed_lock:
        with open(PROCESSED_STORE, "w", encoding="utf-8") as f:
            json.dump(list(processed_ids), f)

def gerar_audio(nome, valor):
    audio_id = make_id(nome, valor)
    path = wav_path(audio_id)
    if not os.path.exists(path):
        frase = frase_pix(nome, valor)
        # gTTS gera MP3 → convertemos para WAV
        temp_mp3 = os.path.join(AUDIO_DIR, f"{audio_id}.mp3")
        gTTS(frase, lang="pt").save(temp_mp3)
        sound = AudioSegment.from_mp3(temp_mp3)
        sound.export(path, format="wav")
        os.remove(temp_mp3)  # limpa o MP3 temporário
    return audio_id, f"/audio/{audio_id}.wav"

def notificar_esp(audio_url, payment_id):
    url = f"{ESP_BASE}{ESP_PLAY_PATH}"
    headers = {}
    if ESP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {ESP_AUTH_TOKEN}"
    payload = {"audio_url": audio_url, "payment_id": payment_id}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
        return r.status_code in (200, 204)
    except Exception as e:
        app.logger.warning(f"Erro ao notificar ESP: {e}")
        return False

# Endpoints
@app.route("/tts", methods=["POST"])
def tts():
    d = request.get_json(force=True)
    nome, valor = d.get("nome","").strip(), d.get("valor_texto","").strip()
    if not nome or not valor: return jsonify({"error":"faltam campos"}), 400
    audio_id, audio_url = gerar_audio(nome, valor)
    return jsonify({"audio_id": audio_id, "audio_url": audio_url})

@app.route("/audio/<audio_id>.wav")
def audio(audio_id):
    path = wav_path(audio_id)
    if not os.path.exists(path): return jsonify({"error":"não encontrado"}), 404
    return send_file(path, mimetype="audio/wav")

@app.route("/health")
def health(): return jsonify({"status":"ok"})

# Mercado Pago monitor
def buscar_pagamentos_once():
    if not MP_ACCESS_TOKEN: return
    headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
    try:
        r = requests.get(MP_SEARCH_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        app.logger.warning(f"Erro MP: {e}")
        return

    for p in body.get("results", []):
        pid = str(p.get("id"))
        if not pid or pid in processed_ids: continue
        if p.get("status") == "approved" and p.get("payment_method_id") == "pix":
            nome = p.get("payer", {}).get("first_name", "Cliente")
            valor = str(p.get("transaction_amount", ""))
            audio_id, audio_url = gerar_audio(nome, valor)
            processed_ids.add(pid); save_processed()
            ok = notificar_esp(audio_url, pid)
            app.logger.info(f"Pix {pid} | Áudio: {audio_url} | ESP OK: {ok}")

def monitor_loop():
    while True:
        try: buscar_pagamentos_once()
        except Exception as e: app.logger.warning(f"Erro monitor: {e}")
        time.sleep(CHECK_INTERVAL)

# Start
if __name__ == "__main__":
    load_processed()
    threading.Thread(target=monitor_loop, daemon=True).start()
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
