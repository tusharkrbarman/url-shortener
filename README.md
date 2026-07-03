# URL Shortener

Production-shaped URL shortening service built from the product spec in `URL_SHORTENER_PRODUCT_SPEC.md`.

The implementation is Python and supports both fast local development and scaled AWS production. It includes authenticated link creation, public redirects, idempotency, link lifecycle, background validation, usage limits, PostgreSQL/SQLite persistence, Redis/database-backed rate limits, structured JSON logs, health/readiness endpoints, metrics, tests, Docker, GitHub Actions, and Terraform for ECS Fargate, RDS PostgreSQL, and ElastiCache Redis.

## Run Locally

```bash
python -m shortener.main
```

Default API key:

```text
dev-api-key
```

Create a link:

```bash
curl -i -X POST http://localhost:8080/api/v1/links \
  -H "Authorization: Bearer dev-api-key" \
  -H "Idempotency-Key: demo-1" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","customCode":"demo","usageLimit":3}'
```

Run the worker in another process:

```bash
SERVICE_MODE=worker python -m shortener.main
```

Visit:

```bash
curl -i http://localhost:8080/demo
```

## Run Tests

```bash
python -m unittest discover -s tests
```

## Run With Docker

```bash
docker compose up --build
```

Docker Compose runs the API, worker, PostgreSQL, and Redis.

## Important Endpoints

- `POST /api/v1/links`: create link, requires `Authorization` and `Idempotency-Key`.
- `GET /api/v1/links/{shortCode}`: metadata, requires `Authorization`.
- `POST /api/v1/links/{shortCode}/disable`: disable link, requires `Authorization`.
- `GET /{shortCode}`: public redirect.
- `GET /healthz`: liveness.
- `GET /readyz`: dependency readiness.
- `GET /metrics`: basic Prometheus-style gauges.

## Configuration

See `.env.example` for runtime configuration.

For production, set at minimum:

- `APP_ENV=production`
- `BASE_URL`
- `DATABASE_PATH`
- `DATABASE_BACKEND=postgres`
- `DATABASE_URL`
- `REDIS_URL`
- `RATE_LIMIT_BACKEND=redis`
- `API_KEYS`
- `CREATE_RATE_LIMIT`
- `METADATA_RATE_LIMIT`
- `REDIRECT_RATE_LIMIT`

## Architecture Notes

The service supports SQLite for local tests and PostgreSQL for production. The important correctness behavior is implemented with database transactions and atomic updates:

- custom code uniqueness uses a unique database constraint;
- idempotent create uses a unique `(owner_id, key)` constraint;
- usage-limit enforcement uses one atomic `UPDATE ... WHERE usage_count < usage_limit RETURNING ...`;
- rate limiting uses transactional counters locally or Redis atomic counters in production.

For AWS production, use `DATABASE_BACKEND=postgres` and `RATE_LIMIT_BACKEND=redis`.

## AWS

See `AWS_DEPLOYMENT.md` for AWS deployment options.

The production AWS path is ECS Fargate for API and worker tasks, RDS PostgreSQL for durable data, and ElastiCache Redis for distributed rate limiting. Terraform lives in `infra/terraform`; deployment notes are in `AWS_DEPLOYMENT.md`.
