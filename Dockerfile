FROM python:3.11-slim

WORKDIR /app

# Install dependencies first so unrelated source edits don't bust the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default DB path inside the container — override at runtime by mounting a
# Railway Volume at /data and setting COTC_DB_PATH=/data/cotc.sqlite.
ENV COTC_DB_PATH=/app/data/cotc.sqlite
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot"]
