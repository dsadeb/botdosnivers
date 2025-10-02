# Força Python 3.11
FROM python:3.11-slim

# Opcional, mas útil pros logs e TZ
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

# (Opcional) instala tzdata pra TZ funcionar 100%
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Comando de start
CMD ["python", "main.py"]
