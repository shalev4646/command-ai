# Deploying CommandAI to Fly.io

Self-hosting on Fly gives us the clean, branded boot that Streamlit Community
Cloud can't: we own `index.html` (olive splash from the first byte, no dark
skeleton) and there is **no "Hosted with Streamlit" badge** at all.

Everything below runs on **your** machine with **your** Fly account — a few
one-time commands, then `fly deploy` whenever you want to ship.

---

## 1. Install flyctl (one time)

PowerShell:

```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

Then restart the terminal so `fly` is on PATH. Check: `fly version`.

## 2. Sign in (one time)

```powershell
fly auth signup     # first time — creates the account + adds a card
# or, if you already have an account:
fly auth login
```

Fly requires a card even on the cheap plan, but a single always-on
`shared-cpu-1x / 1GB` machine runs about **$5/month** (less if you drop to
512 MB — see §6).

## 3. Create the app (one time)

From the project folder (it already has `fly.toml` + `Dockerfile`):

```powershell
fly launch --no-deploy --copy-config
```

- When asked, pick a **unique app name** (e.g. `commandai-idf`) and region
  **`fra`** (Frankfurt — closest to Israel).
- Say **no** to Postgres/Redis/any add-ons.
- It updates `fly.toml` with your chosen name.

## 4. Set the secrets

The API key is **required** (without it the app loads but can't answer):

```powershell
fly secrets set ANTHROPIC_API_KEY="sk-ant-...your key from .env..."
```

Optional — only if you want the admin dashboard (`?admin=1`) and Google Sheets
metrics logging. This ships your whole local `.streamlit/secrets.toml` as one
secret; the container writes it back to a file at startup:

```powershell
fly secrets set STREAMLIT_SECRETS_TOML="$(Get-Content -Raw .streamlit/secrets.toml)"
```

## 5. Deploy

```powershell
fly deploy --remote-only --ha=false
```

`--remote-only` builds the image on Fly's builder (no local Docker needed).
First build takes a few minutes (it installs the wheels and bakes the
embedding model + branded `index.html` into the image).

**`--ha=false` is important, not cosmetic.** Without it Fly's first deploy of
an HTTP service creates **two** machines. The app's daily question cap lives in
each process's memory (it resets on reboot — that's fine), so two machines mean
two independent counters: the real budget guard silently doubles (a 50/day cap
becomes 100/day across the pair) and the admin dashboard sees only the half
that hit its machine. One machine keeps the cap honest. If you already deployed
and `fly status` shows two, run `fly scale count 1`. Then:

```powershell
fly open        # opens https://<your-app>.fly.dev
```

## 6. Verify + tune

- Open the URL — the boot should be **olive → CommandAI splash → entry**, with
  no dark flash and no Streamlit badge.
- Logs: `fly logs`. Status: `fly status`.
- **Cost trim (optional):** try 512 MB to save ~$2.50/mo —
  `fly scale memory 512`. If you see `Out of memory` in `fly logs`, go back up:
  `fly scale memory 1024`.

## 7. iPhone home-screen icon (important)

The app is on a **new URL** with a fresh manifest + launch images, so the old
icon is stale:

1. Delete the old CommandAI icon from the home screen.
2. Open the new `https://<your-app>.fly.dev` in **Safari**.
3. Share → **Add to Home Screen**.

iOS snapshots the branded manifest + olive launch images at add-time — that's
what makes the icon-tap boot fully branded.

## Redeploying later

After any code change:

```powershell
fly deploy --remote-only
```

Always-on means no cold start — visitors get the warm, branded load every time.

## Optional: custom domain

```powershell
fly certs add app.yourdomain.com     # then add the shown DNS records
```
