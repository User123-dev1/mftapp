# Enterprise App Portal

One landing page for every internal application: Managed File Transfer, Asset
Manager, Label Generator, Service Desk, and anything added later.

Features:
- Cards for each app, with search (press `/`) and category filters
- Live status for each app (Online / Unreachable), checked by the server
- Favorites pinned to the top, saved per browser
- Light and dark themes, works on phones

## Run

```bash
cd enterprise_portal
pip install -r requirements.txt
python portal_server.py          # http://localhost:8080
```

Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PORTAL_PORT` | `8080` | Port to listen on |
| `PORTAL_HOST` | `0.0.0.0` | Interface to bind |
| `PORTAL_APPS_FILE` | `apps.json` | Path to the app catalog |
| `PORTAL_HEALTH_TIMEOUT` | `3` | Seconds before a health check gives up |
| `PORTAL_HEALTH_CACHE` | `30` | Seconds to reuse a health result |

## Adding or changing apps

Edit `apps.json`. It is re-read on each request, so you don't need to restart.

```json
{
  "id": "hr-portal",
  "name": "HR Portal",
  "description": "Payslips, leave requests and policies.",
  "url": "https://hr.scepces.com/",
  "health_url": "https://hr.scepces.com/health",
  "category": "People",
  "icon": "badge",
  "color": "#9333ea",
  "owner": "Human Resources"
}
```

- `id`, `name`, `url` are required; `id` must be unique.
- `health_url` is optional. Without it, the portal checks `url`. Any response
  below HTTP 500 counts as online, so login redirects and 401s still show
  the app as up.
- `icon` is a [Material Symbols](https://fonts.google.com/icons) name. If the
  icon font can't load (no internet access), the first letter of the app
  name is shown.

## API

| Endpoint | Description |
|---|---|
| `GET /` | Portal page |
| `GET /api/apps` | App catalog |
| `GET /api/status?refresh=true` | Health of every app (`refresh` skips the cache) |
| `GET /health` | Portal's own health |

## Tests

```bash
python -m pytest test_portal.py
```
