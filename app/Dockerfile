FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin aitra \
 && mkdir -p /app/data && chown aitra:aitra /app/data

COPY VERSION wsgi.py ./
COPY aitra ./aitra
COPY static ./static

USER aitra
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=4).status==200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:8787", "--workers", "1", "--threads", "4", "--access-logfile", "-", "wsgi:app"]
