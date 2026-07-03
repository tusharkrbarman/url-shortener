# AWS Deployment Guide

This service is built in Python and runs as a container. It exposes one API process and one worker process from the same image, selected with `SERVICE_MODE`.

## Option 1: EC2 With Docker Compose

This is the fastest AWS deployment path for the current self-contained implementation.

Use this when:

- You want a simple deploy for a hackathon or first production-shaped demo.
- One API instance is acceptable.
- SQLite persistence on an attached EBS volume is acceptable.

AWS resources:

- EC2 instance.
- Security group allowing inbound `80` or `443`.
- EBS volume mounted for persistent data.
- Optional CloudWatch agent for logs.
- Optional Application Load Balancer in front of EC2.

Recommended runtime values:

```text
APP_ENV=production
PORT=8080
BASE_URL=https://your-domain.example
DATABASE_PATH=/app/data/shortener.db
API_KEYS=<secret-api-key>
SERVICE_MODE=api
VALIDATION_ENABLED=true
LOG_LEVEL=info
```

Deployment steps:

1. Build and push the image to Amazon ECR, or copy the repository to the EC2 instance.
2. Mount an EBS-backed directory for persistent data.
3. Run `docker compose up -d --build`.
4. Configure a reverse proxy or load balancer to forward traffic to port `8080`.
5. Check `GET /healthz` and `GET /readyz`.

Important note: SQLite is reliable for this single-node deployment, but it is not the best fit for horizontally scaled API containers.

## Option 2: ECS Fargate With RDS And ElastiCache

This is the recommended AWS production architecture.

AWS resources:

- ECR repository for the container image.
- ECS cluster.
- ECS service for the API.
- ECS service for the worker.
- Application Load Balancer for API traffic.
- RDS PostgreSQL for durable data.
- ElastiCache Redis for distributed rate limits and background queue state.
- Secrets Manager or SSM Parameter Store for secrets.
- CloudWatch Logs for structured JSON logs.

Container modes:

```text
SERVICE_MODE=api
SERVICE_MODE=worker
```

Health checks:

- ALB target group health check: `GET /healthz`.
- Deployment readiness check: `GET /readyz`.

Production storage note:

The current code ships with a standard-library SQLite adapter so the project runs without dependency downloads. For a horizontally scaled ECS deployment, add a PostgreSQL adapter and Redis-backed queue/rate limiter while preserving the API contract and tests.

## Suggested AWS Secret Values

Store these in Secrets Manager or SSM Parameter Store:

- `API_KEYS`
- `DATABASE_URL`, when using RDS PostgreSQL
- `REDIS_URL`, when using ElastiCache Redis
- External validation provider credentials, if added

## Operational Checks

After deployment:

```bash
curl -i https://your-domain.example/healthz
curl -i https://your-domain.example/readyz
curl -i https://your-domain.example/metrics
```

Create a link:

```bash
curl -i -X POST https://your-domain.example/api/v1/links \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: aws-demo-1" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","customCode":"awsdemo","usageLimit":5}'
```

