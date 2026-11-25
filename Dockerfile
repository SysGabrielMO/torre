# Torre en un servidor: pide usuario y contraseña y guarda los datos en /data.
# Va detrás de un proxy con HTTPS (p. ej. Caddy); el puerto 8000 no se publica.
#
#   docker build -t torre .
#   docker run -d --name torre -v torre_data:/data -e TORRE_ALLOWED_HOSTS=torre.midominio.com torre
#   docker exec -it torre python -m torre.users añadir TU_USUARIO
#
# Funciona en x86 y en ARM (p. ej. AWS t4g).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Madrid \
    TORRE_DB=/data/torre.db \
    TORRE_BACKUP_DIR=/data/copias \
    TORRE_AUTH=1 \
    TORRE_NO_REMINDERS=1

# tzdata: las fechas límite y las copias diarias van con la hora de TZ, no en UTC
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY torre ./torre
RUN useradd --system --create-home --uid 10001 torre \
 && mkdir -p /data && chown torre:torre /data
USER torre
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=1m --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=4)"

# --proxy-headers: la IP real del visitante (para el límite de intentos) y el
# https llegan del proxy; solo el proxy puede hablar con este contenedor
CMD ["uvicorn", "torre.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
