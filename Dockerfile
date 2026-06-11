FROM python:3.12-slim

# Keep Python output unbuffered (logs appear immediately) and skip .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FM_COMPARE_DATA_DIR=/data

# LibreOffice headless for formula recalculation (Stage 2 sensitivity analysis).
# libreoffice-calc pulls in libreoffice-common which provides the `soffice` binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-calc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fm_compare ./fm_compare

# App data (settings, logs, uploads, dictionary) lives on a volume.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "fm_compare.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
