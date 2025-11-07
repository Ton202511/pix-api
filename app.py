from flask import Flask, request
import requests

app = Flask(__name__)

# IP do ESP32 na rede local ou pública
ESP32_IP = "http://192.168.0.123"  # substitua pelo IP real

@app.route('/')
def home():
    return "API Pix ativa!"

@app.route('/pix', methods=['POST'])
def pix_recebido():
    # Aqui você pode validar o conteúdo do Pix recebido
    print("🔔 Pix recebido!")

    # Envia comando para ESP32
    try:
        r = requests.get(f"{ESP32_IP}/play")
        print("🎵 Comando enviado ao ESP32:", r.status_code)
    except Exception as e:
        print("❌ Erro ao enviar comando:", e)

    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
