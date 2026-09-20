FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEAKHUNTER_DATA_DIR=/data \
    LEAKHUNTER_DB_PATH=/data/leakhunter.db
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)" || exit 1
CMD ["streamlit","run","app.py","--server.address=0.0.0.0","--server.port=8501","--server.headless=true","--server.enableXsrfProtection=true","--server.enableCORS=false"]
