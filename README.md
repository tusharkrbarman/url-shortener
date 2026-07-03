# URL Shortener

Production-shaped URL shortening service built from the product spec in `URL_SHORTENER_PRODUCT_SPEC.md`.

The implementation uses only the Python standard library so it can run in constrained environments without dependency downloads. It includes authenticated link creation, public redirects, idempotency, link lifecycle, background validation, usage limits, SQLite persistence, concurrent-safe counters, structured JSON logs, health/readiness endpoints, metrics, tests, Docker, and AWS deployment guidance.

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
- `API_KEYS`
- `CREATE_RATE_LIMIT`
- `METADATA_RATE_LIMIT`
- `REDIRECT_RATE_LIMIT`

## Architecture Notes

SQLite is used here to keep the project fully self-contained. The important correctness behavior is implemented with database transactions and atomic updates:

- custom code uniqueness uses a unique database constraint;
- idempotent create uses a unique `(owner_id, key)` constraint;
- usage-limit enforcement uses one atomic `UPDATE ... WHERE usage_count < usage_limit RETURNING ...`;
- rate limiting uses transactional counters.

For larger production scale, replace the storage adapter with PostgreSQL and move rate-limit counters and queue state to Redis. The API and test behavior should remain the same.

## AWS

See `AWS_DEPLOYMENT.md` for AWS deployment options.

For the current self-contained Python implementation, the simplest AWS path is EC2 with Docker Compose and an attached EBS volume mounted at `/app/data`.

For higher-scale production use on AWS, run the API and worker on ECS Fargate, use RDS PostgreSQL for durable data, and use ElastiCache Redis for distributed rate limits and queue state. Keep `/healthz` for liveness and use `/readyz` for dependency-aware deployment verification.
