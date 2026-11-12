# TadDee in one container: web UI + API on port 8040, data in /data.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TADDEE_PORT=8040 TADDEE_HOST=0.0.0.0 \
    TADDEE_DATA_DIR=/data TADDEE_BACKUP_DIR=/backups \
    TZ=Asia/Bangkok
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY cam cam
COPY core core
COPY nesting nesting
COPY relief relief
COPY web web
COPY samples samples
VOLUME ["/data", "/backups"]
EXPOSE 8040
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/api/health'%os.environ['TADDEE_PORT'],timeout=4)"
CMD ["python", "-m", "app"]
