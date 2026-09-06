# Roamlight — a family photo library.
#
# ⚠ The heavy lifting is done by three programs, not by Python: libvips
#   (resizing), exiftool (metadata) and ffmpeg (video). They are the reason
#   this image is ~450 MB and not 80 — a "slim" image without them would
#   simply fail on the first photograph.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FAMILY_BASE=/app \
    FAMILY_DATA=/data \
    FAMILY_DB=/data/roamlight.db \
    FAMILY_DERIVATIVES=/data/derivatives \
    FAMILY_INCOMING=/data/incoming \
    FAMILY_ORIGINS=/originals \
    FAMILY_WEB=/library \
    FAMILY_AUTH=local \
    FAMILY_REQUIRE_MOUNT=0 \
    FAMILY_CLAMAV=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        libvips42 \
        libimage-exiftool-perl \
        ffmpeg \
        sqlite3 \
        gosu \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY templates/ ./templates/
COPY static/ ./static/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080
VOLUME ["/data", "/originals", "/library"]

# ⚠ The health check is what tells a restart policy that the site is alive.
#   /api/health answers without a login on purpose (it says nothing private
#   unless the request comes from the machine itself).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
