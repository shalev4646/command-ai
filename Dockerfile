# CommandAI — self-hosted Streamlit image (Fly.io).
# Self-hosting is what lets us own the served index.html: we brand it at build
# time so the very first byte the browser paints is the olive splash (no stock
# Streamlit skeleton), and there is no Community-Cloud "Hosted with Streamlit"
# badge layer at all.
FROM python:3.12-slim

# onnxruntime needs libgomp; curl is used by the Fly health check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so the (slow) wheel install layer caches across code edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source (secrets excluded via .dockerignore).
COPY . .

# 1) Brand Streamlit's static index.html — olive splash from t=0, every request.
RUN python -c "import boot_shell, sys; sys.exit(0 if boot_shell.patch_index_html() else 1)"

# 2) Prebuild the vector index: downloads the ~120MB multilingual-MiniLM ONNX
#    model into the image and validates the ingest pipeline, so the always-on
#    container never fetches the model at runtime. Embedding is fully local —
#    no ANTHROPIC_API_KEY needed here.
RUN python -c "import backend; backend.ensure_pdfs_ingested(); print('chunks:', backend.warm_index())"

# Streamlit server config (Fly terminates TLS and forwards to internal_port).
ENV STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false

EXPOSE 8080

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["streamlit", "run", "app.py"]
