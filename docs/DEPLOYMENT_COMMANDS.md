# DEPLOYMENT_COMMANDS.md

Linux-compatible commands for Hostinger VPS or EasyPanel shell access. Replace placeholders locally; do not commit secrets.

## Docker Build

```bash
docker build -t inbound-ai-voice:staging .
```

## Docker Run

```bash
docker run -d \
  --name inbound-ai-voice \
  --restart unless-stopped \
  --env-file /opt/inbound-ai/.env.production \
  -p 8000:8000 \
  -p 8081:8081 \
  inbound-ai-voice:staging
```

## Health Check

```bash
curl -fsS http://127.0.0.1:8000/health | python -m json.tool
```

For EasyPanel domain routing:

```bash
curl -fsS https://<staging-domain>/health | python -m json.tool
```

## Supervisor Status

```bash
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status
```

## Restart Container

```bash
docker restart inbound-ai-voice
```

## Log Inspection

```bash
docker logs --tail=200 -f inbound-ai-voice
```

## PostgreSQL Connectivity Check

From the VPS:

```bash
psql "$DATABASE_URL" -c "SELECT 1;"
```

From the app health surface:

```bash
curl -fsS http://127.0.0.1:8000/health | python -m json.tool
```

## Container Restart Validation

```bash
docker restart inbound-ai-voice
sleep 10
docker ps --filter name=inbound-ai-voice
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status
curl -fsS http://127.0.0.1:8000/health | python -m json.tool
```

## Supervisor Recovery Validation

```bash
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf stop agent
sleep 10
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status agent
```

```bash
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf stop ui_server
sleep 10
docker exec inbound-ai-voice supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status ui_server
```

Expected: stopped programs return to `RUNNING` because `autorestart=true`.
