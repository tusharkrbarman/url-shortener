# Production-Ready URL Shortening Service

## 1. Purpose

Build a production-ready URL shortening service that allows authenticated users to create and manage short links, while allowing unauthenticated public users to visit short links and be redirected to their original destinations.

The service must be reliable, observable, safe under concurrent access, configurable through environment variables, testable, containerized, and deployable to AWS.

This document is intended as the product and architecture reference for implementation.

## 2. Goals

- Create short links for long destination URLs.
- Redirect public visitors from short links to their original URLs.
- Store link metadata and usage data reliably.
- Track visits and enforce optional usage limits.
- Support asynchronous URL validation or enrichment after link submission.
- Protect creation and metadata APIs with authentication.
- Enforce rate limits for abuse-prone operations.
- Provide health and readiness endpoints for operations.
- Emit structured logs and operational metrics.
- Include automated tests for core behavior, concurrency, and failure cases.
- Support containerized local and production deployment.

## 3. Non-Goals For The First Version

- Full user registration and password management.
- Billing or usage plans.
- Custom domains per user.
- Advanced analytics dashboards.
- Browser extensions.
- Link preview pages.
- Distributed tracing as a hard dependency.

These can be added later without changing the core service model.

## 4. Primary Users

### Authenticated API User

Creates short links, views metadata, configures optional usage limits, and checks link state.

### Public Redirect Visitor

Uses a short URL and is redirected to the original destination when the link is active and eligible.

### Operator

Deploys, monitors, debugs, scales, and maintains the service.

## 5. Core Concepts

### Short Link

A persisted record that maps a short code to a long destination URL.

### Short Code

The unique path segment used in public redirects.

Example:

```text
https://sho.rt/aB93kQ
```

### Link Lifecycle

Each link moves through a lifecycle:

- `pending`: Link was accepted and stored, but validation or enrichment has not completed.
- `active`: Link passed validation and can redirect visitors.
- `failed`: Link validation failed or enrichment marked it unusable.
- `disabled`: Link was manually or automatically disabled.

### Usage Limit

An optional maximum number of successful redirects allowed for a link.

When the limit is reached, the link must stop redirecting.

### Idempotency Key

A client-provided key used to safely retry link creation without creating duplicate links.

## 6. Functional Requirements

### Link Creation

- Authenticated users can create short links.
- Required input:
  - `url`: Long destination URL.
- Optional input:
  - `customCode`: Desired short code.
  - `usageLimit`: Maximum number of successful redirects.
  - `expiresAt`: Future expiration timestamp.
  - `metadata`: Client-owned labels or tags.
- The service validates basic input synchronously before persisting.
- The service stores the original request even if asynchronous validation later fails.
- The service returns a stable response for repeated requests with the same idempotency key.
- Duplicate custom short codes are rejected cleanly with a conflict response.
- Generated short codes must be unique.

### Redirects

- Public redirects do not require authentication.
- Redirects are allowed only when the link is `active`.
- Redirects are denied when the link is:
  - `pending`
  - `failed`
  - `disabled`
  - expired
  - over its usage limit
  - unknown
- Successful redirects increment usage count exactly once.
- Usage-limit enforcement must remain correct under concurrent traffic.
- Redirect responses should use `302 Found` by default.
- The service may support configurable redirect status codes later, such as `301`, `302`, `307`, or `308`.

### Metadata Access

- Authenticated users can fetch basic information about their short links.
- Metadata includes:
  - short code
  - destination URL
  - lifecycle status
  - creation timestamp
  - update timestamp
  - validation status and failure reason, if any
  - usage count
  - usage limit, if set
  - expiration timestamp, if set
- Metadata access must not expose another user's links unless explicitly supported by an admin role.

### Lifecycle Management

- Newly created links start as `pending`.
- Background validation moves links to `active` or `failed`.
- Operators or authenticated owners may disable links.
- Disabled links do not redirect.
- Failed links remain inspectable through authenticated metadata APIs.

### Asynchronous Validation And Enrichment

The service should support a background worker that validates or enriches submitted URLs.

Examples:

- Check whether the URL uses `http` or `https`.
- Reject private network destinations if SSRF protection is enabled.
- Fetch the page title.
- Detect unreachable destinations.
- Detect blocked domains.
- Run malware or abuse checks through an external provider.

External dependency failures must not lose the original request. The link remains persisted as `pending`, and the validation job is retried.

### Authentication

- Link creation and metadata APIs require authentication.
- Public redirects do not require authentication.
- Authentication is configurable through environment variables.
- First implementation recommendation:
  - API key authentication using an `Authorization: Bearer <token>` header.
- Future compatible options:
  - JWT validation.
  - OAuth/OIDC integration.
  - User-scoped API keys.

### Rate Limiting

Rate limits are required for write-heavy or abuse-prone operations.

Rate-limited operations:

- Link creation.
- Metadata listing or lookup.
- Optional: redirect endpoint by IP or short code to reduce abuse.

Rate-limit responses must be clear:

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Too many requests. Please retry later.",
    "requestId": "req_..."
  }
}
```

Rate limiting must be safe under concurrent requests. In production, Redis-backed counters are preferred over in-memory counters.

## 7. Recommended Architecture

```text
Client / Public Visitor
        |
        v
API Service
        |
        +--> PostgreSQL
        |
        +--> Redis
        |      |
        |      +--> Rate limit counters
        |      +--> Background job queue
        |
        +--> Structured logs / metrics
        |
        v
Background Worker
        |
        +--> External URL validation or enrichment providers
```

### Components

### API Service

Responsibilities:

- Authenticate protected routes.
- Validate request payloads.
- Enforce rate limits.
- Create and fetch links.
- Serve public redirects.
- Emit structured logs and metrics.
- Expose health and readiness endpoints.

### Database

Recommended: PostgreSQL.

Responsibilities:

- Persist links.
- Persist idempotency keys.
- Persist usage counters.
- Persist validation job state.
- Provide transactional correctness for concurrent writes and redirects.

### Redis

Recommended for production.

Responsibilities:

- Distributed rate limiting.
- Background job queue.
- Optional short-lived cache for active links.

### Background Worker

Responsibilities:

- Process validation and enrichment jobs.
- Retry transient failures.
- Mark links `active` or `failed`.
- Record job attempts and failure reasons.
- Emit observable job events.

## 8. Data Model

### `links`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `owner_id` | Text / UUID | Authenticated owner or API client |
| `short_code` | Text | Unique |
| `destination_url` | Text | Original URL |
| `status` | Enum | `pending`, `active`, `failed`, `disabled` |
| `usage_count` | Integer | Successful redirects |
| `usage_limit` | Integer nullable | Optional cap |
| `expires_at` | Timestamp nullable | Optional expiration |
| `validation_error` | Text nullable | Safe error summary |
| `metadata` | JSONB | Optional client or enrichment metadata |
| `created_at` | Timestamp | Server generated |
| `updated_at` | Timestamp | Server generated |

Indexes:

- Unique index on `short_code`.
- Index on `owner_id`.
- Index on `status`.
- Index on `expires_at`.

### `idempotency_keys`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `owner_id` | Text / UUID | Authenticated owner |
| `key` | Text | Client-provided idempotency key |
| `request_hash` | Text | Hash of normalized request body |
| `response_body` | JSONB | Original successful response |
| `status_code` | Integer | Original response status |
| `created_at` | Timestamp | Server generated |
| `expires_at` | Timestamp | Retention expiry |

Indexes:

- Unique index on `(owner_id, key)`.
- Index on `expires_at`.

Behavior:

- If the same owner sends the same idempotency key with the same request body, return the original response.
- If the same owner sends the same idempotency key with a different body, return `409 Conflict`.
- Retain idempotency keys for a configurable period, such as 24 hours.

### `validation_jobs`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `link_id` | UUID | Link being validated |
| `status` | Enum | `queued`, `processing`, `succeeded`, `failed`, `retrying`, `dead` |
| `attempt_count` | Integer | Number of attempts |
| `next_run_at` | Timestamp | Retry scheduling |
| `last_error` | Text nullable | Safe error summary |
| `created_at` | Timestamp | Server generated |
| `updated_at` | Timestamp | Server generated |

## 9. API Contract

### Create Short Link

```http
POST /api/v1/links
Authorization: Bearer <token>
Idempotency-Key: <unique-client-key>
Content-Type: application/json
```

Request:

```json
{
  "url": "https://example.com/very/long/path",
  "customCode": "launch",
  "usageLimit": 100,
  "expiresAt": "2026-12-31T23:59:59Z",
  "metadata": {
    "campaign": "winter"
  }
}
```

Response:

```json
{
  "id": "link_123",
  "shortCode": "launch",
  "shortUrl": "https://sho.rt/launch",
  "status": "pending",
  "usageCount": 0,
  "usageLimit": 100,
  "expiresAt": "2026-12-31T23:59:59Z",
  "createdAt": "2026-07-03T10:00:00Z"
}
```

Expected status codes:

- `201 Created`: New link created.
- `200 OK`: Existing idempotent response returned.
- `400 Bad Request`: Invalid input.
- `401 Unauthorized`: Missing or invalid authentication.
- `409 Conflict`: Duplicate custom code or idempotency-key body mismatch.
- `429 Too Many Requests`: Rate limit exceeded.
- `500 Internal Server Error`: Unexpected server error.

### Redirect

```http
GET /{shortCode}
```

Behavior:

- If link is active and eligible, increment usage count and return redirect.
- If link is pending, return `409 Conflict` or `425 Too Early`.
- If link failed, disabled, expired, unknown, or usage-limited, return a clear non-redirect response.

Recommended responses:

- `302 Found`: Successful redirect.
- `404 Not Found`: Unknown short code.
- `409 Conflict`: Link is pending, failed, disabled, or over limit.
- `410 Gone`: Link expired.

### Get Link Metadata

```http
GET /api/v1/links/{shortCode}
Authorization: Bearer <token>
```

Response:

```json
{
  "id": "link_123",
  "shortCode": "launch",
  "destinationUrl": "https://example.com/very/long/path",
  "status": "active",
  "usageCount": 42,
  "usageLimit": 100,
  "expiresAt": "2026-12-31T23:59:59Z",
  "validationError": null,
  "metadata": {
    "campaign": "winter",
    "title": "Example Domain"
  },
  "createdAt": "2026-07-03T10:00:00Z",
  "updatedAt": "2026-07-03T10:00:05Z"
}
```

Expected status codes:

- `200 OK`: Metadata returned.
- `401 Unauthorized`: Missing or invalid authentication.
- `403 Forbidden`: Authenticated caller does not own the link.
- `404 Not Found`: Link not found.
- `429 Too Many Requests`: Rate limit exceeded.

### Disable Link

```http
POST /api/v1/links/{shortCode}/disable
Authorization: Bearer <token>
```

Behavior:

- Sets status to `disabled`.
- Future redirects are rejected.
- Metadata remains available.

### Health

```http
GET /healthz
```

Returns `200 OK` if the API process is alive.

This endpoint should not require database or Redis access.

### Readiness

```http
GET /readyz
```

Returns `200 OK` only when required dependencies are reachable.

Checks:

- PostgreSQL connection.
- Redis connection, if Redis is required.
- Background queue availability, if separate from Redis.

Returns `503 Service Unavailable` when required dependencies are unavailable.

## 10. Concurrency And Correctness

### Short Code Creation

Generated short codes must be inserted with a unique database constraint.

If a generated code collides:

1. Retry generation.
2. Attempt insert again.
3. Fail only after a small bounded number of attempts.

Custom codes should return `409 Conflict` if already taken.

### Idempotent Creation

Creation must be protected by a transaction.

Recommended flow:

1. Authenticate caller.
2. Normalize and hash request body.
3. Insert idempotency key row with unique `(owner_id, key)`.
4. If insert succeeds, create link and store response body in the idempotency row.
5. If insert conflicts, fetch existing idempotency row.
6. If request hash matches, return stored response.
7. If request hash differs, return `409 Conflict`.

### Usage Limit Enforcement

Redirects must enforce limits atomically in the database.

Recommended SQL pattern:

```sql
UPDATE links
SET usage_count = usage_count + 1,
    updated_at = now()
WHERE short_code = $1
  AND status = 'active'
  AND (expires_at IS NULL OR expires_at > now())
  AND (usage_limit IS NULL OR usage_count < usage_limit)
RETURNING destination_url, usage_count, usage_limit;
```

If no row is returned, fetch the link to determine whether it is missing, inactive, expired, or over limit.

This avoids race conditions where concurrent redirects exceed the usage limit.

### Rate Limiting

Production rate limiting should use atomic Redis operations or a proven library that uses Redis safely.

Recommended strategies:

- Fixed window for simplicity.
- Sliding window or token bucket for smoother limits.

Rate-limit keys should include:

- Authenticated owner ID for protected routes.
- IP address for unauthenticated public routes, if redirect limits are enabled.
- Operation name.

## 11. Validation And Enrichment Behavior

### Synchronous Validation

Run before persisting:

- URL is present.
- URL is valid.
- URL scheme is `http` or `https`.
- URL length is within configured limit.
- Custom code, if provided, matches allowed pattern.
- Usage limit, if provided, is a positive integer.
- Expiration, if provided, is in the future.

### Asynchronous Validation

Run after persisting:

- DNS resolution.
- Reachability check.
- Optional HEAD or GET request with timeout.
- SSRF protection.
- Blocklist check.
- Title extraction.
- External security provider scan.

### Failure Handling

Transient failures:

- Keep link `pending`.
- Retry with exponential backoff.
- Record attempt count and safe error summary.

Permanent failures:

- Mark link `failed`.
- Store safe failure reason.
- Expose failure reason in metadata.

Dead jobs:

- Mark job `dead`.
- Emit structured log event.
- Expose metric.
- Preserve original link record.

## 12. Security Requirements

### Input Safety

- Reject invalid URLs.
- Reject unsupported URL schemes such as `javascript:`, `file:`, `ftp:`, and `data:`.
- Protect against SSRF by blocking private, loopback, link-local, multicast, and metadata service IP ranges during validation.
- Limit maximum URL length.
- Limit maximum metadata size.
- Validate custom codes with an allowlist pattern such as `^[A-Za-z0-9_-]{3,64}$`.

### Authentication Safety

- Never log raw API keys or tokens.
- Use constant-time comparison for static API keys.
- Support key rotation through environment variables or secret management.

### Error Safety

- Do not return stack traces to clients.
- Include a request ID in error responses.
- Log detailed errors internally with structured fields.
- Sanitize external provider errors before storing or returning them.

### Logging Safety

- Avoid logging full destination URLs by default.
- Prefer logging:
  - link ID
  - short code
  - owner ID
  - request ID
  - event name
  - status
  - safe error code
- If destination URLs must be logged for debugging, protect this behind an explicit non-production setting.

## 13. Observability

### Structured Logs

Use JSON logs in production.

Every request log should include:

- `timestamp`
- `level`
- `requestId`
- `method`
- `path`
- `statusCode`
- `durationMs`
- `ownerId`, when authenticated

Important event logs:

- `link.created`
- `link.validation.succeeded`
- `link.validation.failed`
- `link.redirect.succeeded`
- `link.redirect.rejected`
- `rate_limit.rejected`
- `background_job.failed`
- `background_job.retrying`
- `dependency.unavailable`

### Metrics

Expose basic metrics directly or document collection through the hosting platform.

Recommended Prometheus-style metrics:

- `http_requests_total`
- `http_request_duration_seconds`
- `links_created_total`
- `redirects_total`
- `redirect_rejections_total`
- `validation_jobs_total`
- `validation_job_failures_total`
- `rate_limit_rejections_total`
- `dependency_health_status`

### Request IDs

- Accept incoming `X-Request-Id` if present.
- Generate one if missing.
- Return it in `X-Request-Id`.
- Include it in logs and error responses.

## 14. Configuration

All runtime configuration should be controlled through environment variables.

| Variable | Required | Example | Description |
| --- | --- | --- | --- |
| `APP_ENV` | Yes | `production` | Runtime environment |
| `PORT` | Yes | `8080` | HTTP port |
| `BASE_URL` | Yes | `https://sho.rt` | Public short URL base |
| `DATABASE_URL` | Yes | `postgres://...` | PostgreSQL connection string |
| `REDIS_URL` | Production | `redis://...` | Redis connection string |
| `AUTH_MODE` | Yes | `api_key` | Authentication mode |
| `API_KEYS` | Yes | `key1,key2` | Accepted API keys or key identifiers |
| `IDEMPOTENCY_TTL_HOURS` | No | `24` | Retention for idempotency records |
| `CREATE_RATE_LIMIT` | No | `60/hour` | Link creation limit |
| `METADATA_RATE_LIMIT` | No | `300/hour` | Metadata access limit |
| `REDIRECT_RATE_LIMIT` | No | `1000/minute` | Optional redirect limit |
| `MAX_URL_LENGTH` | No | `2048` | Maximum destination URL length |
| `MAX_METADATA_BYTES` | No | `4096` | Maximum metadata payload |
| `VALIDATION_ENABLED` | No | `true` | Enables background validation |
| `VALIDATION_TIMEOUT_MS` | No | `5000` | External validation timeout |
| `VALIDATION_MAX_ATTEMPTS` | No | `5` | Max retry attempts |
| `LOG_LEVEL` | No | `info` | Log verbosity |
| `LOG_DESTINATION_URLS` | No | `false` | Debug-only unsafe URL logging |

## 15. Testing Requirements

Automated tests are required.

### Unit Tests

Cover:

- URL validation.
- Custom code validation.
- Request normalization and idempotency hash generation.
- Authentication checks.
- Error response formatting.
- Rate-limit key generation.

### Integration Tests

Cover:

- Authenticated link creation.
- Unauthenticated rejection for protected routes.
- Duplicate custom code conflict.
- Idempotent create retry returns the original response.
- Idempotency key reuse with different body returns conflict.
- Metadata access.
- Lifecycle transitions from `pending` to `active`.
- Lifecycle transition from `pending` to `failed`.
- Redirect behavior for each lifecycle state.
- External validation provider failure keeps the link and retries job.
- Usage limit allows exactly the configured number of redirects.
- Usage limit rejects later redirects.
- Concurrent redirects do not exceed usage limit.
- Concurrent creates do not create duplicate custom codes.
- Rate limit returns `429` and clear error body.
- Readiness fails when PostgreSQL or Redis is unavailable.

### Concurrency Tests

Must specifically test:

- Many simultaneous requests creating the same custom code.
- Many simultaneous requests using the same idempotency key.
- Many simultaneous redirects against a link with a low usage limit.
- Many simultaneous write requests crossing a rate-limit threshold.

### Background Worker Tests

Cover:

- Successful validation activates link.
- Permanent validation failure marks link failed.
- Transient provider failure schedules retry.
- Max attempts moves job to dead state.
- Job failures are logged and counted.

## 16. Container Requirements

The service must include:

- `Dockerfile`.
- `.dockerignore`.
- Runtime image with only production dependencies.
- Non-root container user where possible.
- Health check configuration.
- Graceful shutdown handling.

Recommended container behavior:

- API and worker can run as separate process types from the same image.
- Startup command is configurable.

Example process modes:

```text
SERVICE_MODE=api
SERVICE_MODE=worker
```

## 17. Local Development

Recommended local stack:

- API service.
- PostgreSQL.
- Redis.
- Worker.

Use `docker compose` for local development.

Expected local commands:

```text
docker compose up --build
docker compose run --rm api test
docker compose run --rm api migrate
```

## 18. AWS Deployment

Recommended deployment targets:

- First deployable version: Amazon EC2 with Docker Compose and an attached EBS volume.
- Scaled production version: Amazon ECS Fargate with Amazon RDS PostgreSQL and Amazon ElastiCache Redis.

### Services

- Web service:
  - Runs API.
  - Exposes HTTP port.
  - Has `/healthz` and `/readyz` health checks.
- Worker service:
  - Runs background validation jobs.
  - Does not expose public HTTP traffic.
- Managed PostgreSQL:
  - Stores persistent data.
- Managed Redis:
  - Stores rate-limit counters and background queue data.

AWS mapping:

- API container: ECS Fargate service or EC2 Docker Compose service.
- Worker container: ECS Fargate service or EC2 Docker Compose service with `SERVICE_MODE=worker`.
- Durable database: RDS PostgreSQL for scaled production, or EBS-backed SQLite for the first self-contained deployment.
- Distributed queue and rate limiting: ElastiCache Redis for scaled production.
- Logs: CloudWatch Logs.
- Metrics: CloudWatch metrics or Prometheus scraping of `/metrics`.
- Secrets: AWS Secrets Manager or SSM Parameter Store.
- Public ingress: Application Load Balancer, API Gateway, or reverse proxy on EC2.

### Required Secrets

- `DATABASE_URL`
- `REDIS_URL`
- `API_KEYS`
- External validation provider credentials, if used.

### Health Checks

Web health check:

```text
GET /healthz
```

Readiness check:

```text
GET /readyz
```

The platform health check should use `/healthz` for process liveness. Deployment verification or load balancer readiness should use `/readyz` where supported.

### Deployment Configuration

The repository should include AWS deployment guidance or infrastructure configuration with:

- API service.
- Worker service.
- Environment variables.
- RDS, ElastiCache, or EBS persistence references.
- Health checks.
- Instance size.
- Autoscaling settings if needed.

## 19. Error Response Format

All API errors should use a consistent shape:

```json
{
  "error": {
    "code": "invalid_url",
    "message": "The provided URL is invalid.",
    "requestId": "req_123"
  }
}
```

Guidelines:

- `code` is stable and machine-readable.
- `message` is safe for clients.
- `requestId` maps to logs.
- Internal stack traces are never included.

## 20. Suggested Technology Stack

The implementation should be built with Python. A practical default:

- API: Python with the standard library HTTP server for this self-contained implementation, or FastAPI for a dependency-backed production framework.
- Database: PostgreSQL.
- Queue/rate limiting: Redis.
- ORM/query layer: SQLAlchemy, direct SQL migrations, or a small repository layer.
- Tests: unittest, Pytest, or equivalent.
- Container: Docker.
- Deployment: AWS EC2 for the self-contained first version, or AWS ECS Fargate with RDS and ElastiCache for larger production use.

For highest concurrency correctness, the usage-limit enforcement should rely on database atomic updates rather than application-level locks.

## 21. Acceptance Criteria

The product is ready when:

- Authenticated users can create links.
- Public users can redirect through active links.
- Metadata APIs require authentication.
- Unauthenticated protected requests are rejected.
- Duplicate custom codes return a clear conflict.
- Retried create requests with the same idempotency key are safe.
- Links move through `pending`, `active`, `failed`, and `disabled` states.
- Pending, failed, disabled, expired, and over-limit links do not redirect.
- Usage limits are enforced correctly under concurrent access.
- Background validation retries transient failures.
- External validation failure never loses the original link request.
- Rate limits work under concurrent requests.
- Logs are structured and include request IDs.
- Sensitive values are not logged.
- Health and readiness endpoints exist and behave correctly.
- Automated tests cover lifecycle, retry, dependency failure, usage limit, concurrency, auth, and rate limiting.
- The service runs locally in containers.
- The service can be deployed to AWS with documented configuration.

## 22. Open Implementation Decisions

The team should decide before coding:

- Exact programming language and web framework.
- Whether API keys are global, per-user, or per-tenant.
- Whether redirects should return a branded error page or JSON/text errors.
- Whether validation blocks activation until successful reachability or only validates syntax and safety.
- Whether Redis is mandatory in all environments or optional for local development.
- Metrics endpoint format, such as Prometheus `/metrics`.
- Retention period for idempotency keys, validation jobs, and analytics events.
- Whether link usage analytics should store aggregate counts only or detailed event rows.

## 23. Recommended First Milestone

Build the first production-shaped version with:

- Authenticated link creation.
- Idempotency keys.
- PostgreSQL persistence.
- Public redirects.
- Atomic usage-limit enforcement.
- Basic metadata endpoint.
- Link lifecycle with `pending`, `active`, `failed`, and `disabled`.
- Background validation worker.
- Redis-backed rate limiting and job queue.
- Structured logging with request IDs.
- `/healthz` and `/readyz`.
- Dockerfile and local Docker Compose.
- Integration tests for auth, lifecycle, idempotency, rate limits, and concurrency.
