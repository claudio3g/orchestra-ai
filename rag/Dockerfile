FROM python:3.10-slim

WORKDIR /app

# Installa dipendenze di sistema minime
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice
COPY rag_service.py .
COPY rag_indexer_lib.py .

# Utente non-root per sicurezza
RUN useradd -m -u 1000 raguser && chown -R raguser:raguser /app
USER raguser

EXPOSE 6335

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:6335/health')" || exit 1

CMD ["python", "rag_service.py"]
