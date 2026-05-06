FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN mkdir -p /tmp/webp \
 && useradd --create-home --shell /usr/sbin/nologin app \
 && chown -R app:app /app /tmp/webp
USER app

ENV PORT=8000 \
    HOST=0.0.0.0 \
    STORAGE_DIR=/tmp/webp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"

CMD ["python", "server.py"]
