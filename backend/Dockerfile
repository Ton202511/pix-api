# Usar imagem oficial do Python
FROM python:3.11-slim

# Instalar ffmpeg (necessário para pydub)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Definir diretório de trabalho
WORKDIR /app

# Copiar requirements e instalar dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante do código
COPY . .

# Definir variável de ambiente PORT dentro do container
ENV PORT=10000

# Comando para iniciar o servidor Flask com Gunicorn
CMD gunicorn app:app --bind 0.0.0.0:$PORT
