FROM python:3.11-slim

# tesseract is the offline fallback engine; drop this layer if you only use a vision model.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY pdfocr ./pdfocr
RUN pip install --no-cache-dir --no-deps .

ENV PDFOCR_HOST=0.0.0.0 \
    PDFOCR_PORT=8000 \
    PDFOCR_STORAGE_DIR=/data

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/health', timeout=3).status_code==200 else 1)"

CMD ["pdfocr", "serve"]
