#!/usr/bin/env bash
set -e

# The ANTHROPIC_API_KEY arrives as a plain Fly secret (env var) — backend.py
# reads it straight from the environment, nothing to do here.
#
# The optional st.secrets features (admin dashboard password, Google Sheets
# metrics) are file-based. If a STREAMLIT_SECRETS_TOML secret is provided,
# materialize it into .streamlit/secrets.toml before the server starts. When
# it is absent the app still runs — those features just stay off.
# The base64 form exists for Windows: secrets.toml contains double quotes (the
# Google service-account key), and PowerShell 5.1 loses the quoting when it
# hands such a value to a native exe, so `fly secrets set` sees it split into
# words. Base64 is a single quote-free token that survives the trip intact.
if [ -n "${STREAMLIT_SECRETS_TOML_B64:-}" ]; then
  mkdir -p .streamlit
  printf '%s' "$STREAMLIT_SECRETS_TOML_B64" | base64 -d > .streamlit/secrets.toml
  echo "docker-entrypoint: wrote .streamlit/secrets.toml from STREAMLIT_SECRETS_TOML_B64"
elif [ -n "${STREAMLIT_SECRETS_TOML:-}" ]; then
  mkdir -p .streamlit
  printf '%s' "$STREAMLIT_SECRETS_TOML" > .streamlit/secrets.toml
  echo "docker-entrypoint: wrote .streamlit/secrets.toml from STREAMLIT_SECRETS_TOML"
fi

exec "$@"
