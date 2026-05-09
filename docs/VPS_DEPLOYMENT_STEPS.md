# VPS_DEPLOYMENT_STEPS.md

EasyPanel-first deployment steps for the current single-container runtime.

## 1. VPS Preparation

- Use the Hostinger VPS intended for staging.
- Confirm Docker is available on the VPS.
- Install or open EasyPanel using the current EasyPanel installer/docs.
- Keep the project private while legacy unauthenticated routes exist.

## 2. PostgreSQL Setup

- Create the staging PostgreSQL database.
- Apply the migration:

```bash
psql "$DATABASE_URL" -f migrations/001_initial.sql
```

- Seed the tenant and tenant config before testing `USE_POSTGRES=true`.

## 3. EasyPanel Project Creation

- Create a new EasyPanel project/app for this repository.
- Use Dockerfile-based deployment.
- Expose port `8000` for the app health/API surface.
- Keep the existing container command from the Dockerfile.

## 4. Environment Variable Setup

- Add all required values from `DEPLOYMENT_ENV_CHECKLIST.md`.
- Start first with `USE_POSTGRES=false`.
- Switch to `USE_POSTGRES=true` only after PostgreSQL migration and seed data are ready.

## 5. Docker Deployment

- Before build, verify local-only secrets are not present in `.env` files or logs.
- If `config.json` is used for fallback settings, treat the built image as sensitive.
- Trigger an EasyPanel build from the current repository.
- Confirm the build completes.
- Confirm the container starts without crash loops.

## 6. Health Endpoint Validation

```bash
curl -fsS https://<staging-domain>/health
```

Expected:

- `status` is `ok` or intentionally `degraded`.
- PostgreSQL status is visible.
- Config source is visible.
- Startup validation is visible.

## 7. Supervisor Validation

Inside the container:

```bash
supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status
```

Expected:

- `agent` is `RUNNING`.
- `ui_server` is `RUNNING`.

## 8. Runtime Log Validation

- Open EasyPanel logs.
- Confirm startup logs are visible.
- Confirm no API keys, database passwords, or provider secrets appear.
- Confirm PostgreSQL mode and startup state are visible.

## 9. Restart Validation

- Restart the EasyPanel app.
- Re-check Supervisor status.
- Re-check `/health`.
- Confirm there is no crash loop.

## 10. Rollback Steps

- Set `USE_POSTGRES=false`.
- Redeploy or restart the container.
- Confirm `/health` reports PostgreSQL disabled.
- If the container still fails, redeploy the previous known-good image.
