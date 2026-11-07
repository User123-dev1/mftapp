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

## Docker

```bash
docker build -t enterprise-portal .
docker run -p 8080:8080 enterprise-portal
```

## Hosting on AWS

The portal is one small container, so it runs on any AWS container service.
The recommended setup is **ECS Fargate behind an Application Load Balancer**:

```
staff ──VPN / Direct Connect──▶ internal ALB (HTTPS, ACM cert) ──▶ ECS Fargate task (port 8080)
                                                                     │
                                       health checks to each app ◀───┘ (needs network reach)
```

Things to settle first:

1. **Network reach to the apps.** The status dots come from the server
   calling each app. If the apps run on-premises, the VPC needs a
   Site-to-Site VPN or Direct Connect to that network, plus DNS resolution
   for names like `assets.scepces.com` (a Route 53 Resolver outbound
   endpoint). Without it the portal still works, but every app shows
   "Unreachable". `localhost` URLs in `apps.json` will not work in AWS:
   use the app's real hostname.
2. **Who can open it.** Either keep the ALB internal (reachable only over
   the VPN), or make it internet-facing and turn on ALB authentication
   (OIDC with Microsoft Entra ID, or Amazon Cognito) so staff sign in first.
   Don't expose it publicly without one of these.
3. **Updating the app list.** `apps.json` is baked into the image, so a
   change means rebuilding and redeploying. To change it without a deploy,
   mount it from EFS and point `PORTAL_APPS_FILE` at it.

Deploy steps (replace `<account>` and `<region>`):

```bash
# 1. Push the image to ECR
aws ecr create-repository --repository-name enterprise-portal
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t <account>.dkr.ecr.<region>.amazonaws.com/enterprise-portal:latest .
docker push <account>.dkr.ecr.<region>.amazonaws.com/enterprise-portal:latest
```

2. Create an ECS cluster and a Fargate task definition using that image:
   0.25 vCPU / 0.5 GB is plenty, container port 8080.
3. Create a target group (type IP, port 8080, health check path `/health`)
   and an ALB with an HTTPS listener using an ACM certificate.
4. Create an ECS service (1–2 tasks, private subnets) attached to the
   target group.
5. Add a Route 53 record, e.g. `portal.scepces.com`, pointing at the ALB.

For a lower-cost single server, the same container also runs on one EC2
instance or Lightsail instance with `docker run`, but you lose automatic
restarts across hosts and zero-downtime deploys.

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
pip install pytest
python -m pytest test_portal.py
```
