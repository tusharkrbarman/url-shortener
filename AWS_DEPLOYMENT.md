# AWS Deployment Guide

This service is built in Python and runs as a container. It exposes one API process and one worker process from the same image, selected with `SERVICE_MODE`.

## Production Architecture

The production architecture is ECS Fargate with RDS PostgreSQL and ElastiCache Redis.

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

Runtime values:

```text
APP_ENV=production
PORT=8080
BASE_URL=https://your-domain.example
DATABASE_BACKEND=postgres
DATABASE_URL=<from Secrets Manager>
REDIS_URL=rediss://<elasticache-endpoint>:6379/0
RATE_LIMIT_BACKEND=redis
API_KEYS=<from Secrets Manager>
VALIDATION_ENABLED=true
LOG_LEVEL=info
```

The code still supports SQLite for fast local tests, but scaled production should use `DATABASE_BACKEND=postgres` and `RATE_LIMIT_BACKEND=redis`.

## Suggested AWS Secret Values

Store these in Secrets Manager or SSM Parameter Store:

- `API_KEYS`
- `DATABASE_URL`, when using RDS PostgreSQL
- `REDIS_URL`, when using ElastiCache Redis
- External validation provider credentials, if added

The Terraform stack in `infra/terraform` creates Secrets Manager secrets for `API_KEYS` and `DATABASE_URL` and injects them into ECS tasks as secrets.

## Terraform Deployment

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

## GitHub Actions Deployment

The manual `Deploy AWS` workflow:

1. Authenticates to AWS through OIDC.
2. Ensures an ECR repository exists.
3. Builds and pushes the Docker image.
4. Runs Terraform against `infra/terraform`.

Required GitHub variables:

- `AWS_REGION`
- `BASE_URL`

Required GitHub secrets:

- `AWS_DEPLOY_ROLE_ARN`
- `API_KEYS`
- `DB_PASSWORD`

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
