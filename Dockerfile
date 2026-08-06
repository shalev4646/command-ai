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

# The service worker flag MUST be set before the patch below and must match the
# runtime value in fly.toml, because the shell is patched in BOTH places: here,
# so the baked image already carries the worker (otherwise the first request
# after a deploy — the one iOS makes on launch — gets a shell without it), and
# again at runtime by app._patch_boot_shell(). If the two disagree the runtime
# pass rewrites the shell to match ITSELF, silently stripping the worker the
# build just added. Change this and fly.toml's [env] together, never one alone.
ENV CAI_SW=1

# 1) Brand Streamlit's static index.html — olive splash from t=0, every request.
RUN python -c "import boot_shell, sys; sys.exit(0 if boot_shell.patch_index_html() else 1)"
# Bake the PWA assets into the image. Doing this at runtime instead means a
# freshly deployed container answers 404 for /static/cai/manifest.json until
# the first live session writes it — and iOS asks for the manifest exactly on
# that first launch. A missing manifest costs background_color, which is what
# turned the launch-image dissolve white. Fails the build if generation fails.
RUN python -m pwa_assets

# Mirror the corpus PDFs into static/ so the drawer's orders list can link them
# as ordinary static assets. app.py does this at import too, but baking it means
# a cold container is not doing 80 hard links on the first user's request.
RUN python -m pdf_static

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
# Strip CR: a Windows checkout (git autocrlf, e.g. a fresh deploy worktree)
# ships the script with CRLF, the shebang becomes "bash\r", and the container
# crash-loops with exit 127 — took prod down for ~6min on 2026-07-27.
# .gitattributes now pins *.sh to LF; this is the belt to that suspender.
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["streamlit", "run", "app.py"]
