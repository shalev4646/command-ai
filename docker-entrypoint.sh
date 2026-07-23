#!/usr/bin/env bash
set -e

# The ANTHROPIC_API_KEY arrives as a plain Fly secret (env var) — backend.py
# reads it straight from the environment, nothing to do here.
#
# The optional st.secrets features (admin dashboard password, Google Sheets
# metrics) are file-based. If a STREAMLIT_SECRETS_TOML secret is provided,
# materialize it into .streamlit/secrets.toml before the server starts. When
# it is absent the app still runs — those features just stay off.
if [ -n "${STREAMLIT_SECRETS_TOML:-}" ]; then
  mkdir -p .streamlit
  printf '%s' "$STREAMLIT_SECRETS_TOML" > .streamlit/secrets.toml
  echo "docker-entrypoint: wrote .streamlit/secrets.toml from STREAMLIT_SECRETS_TOML"
fi

exec "$@"
