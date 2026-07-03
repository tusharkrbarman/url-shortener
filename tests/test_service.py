import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shortener.config import Config
from shortener.db import Database, _pg_sql, _split_sql_statements, create_database
from shortener.errors import Conflict, RateLimited, Unauthorized
from shortener.http_api import ShortenerServer
from shortener.rate_limit import RateLimiter
from shortener.service import LinkService, iso_now
from shortener.validation import TransientValidationError, ValidationResult
from shortener.worker import ValidationWorker


class AlwaysTransientValidator:
    def validate(self, url):
        raise TransientValidationError("provider unavailable")


class SuccessValidator:
    def validate(self, url):
        return ValidationResult(metadata={"title": "Example"})


def build_stack(**overrides):
    temp = tempfile.NamedTemporaryFile(delete=True)
    temp.close()
    config = Config(
        database_path=temp.name,
        base_url="http://localhost:8080",
        api_keys=("secret",),
        validation_max_attempts=overrides.pop("validation_max_attempts", 3),
        create_rate_limit=overrides.pop("create_rate_limit", "100/hour"),
        metadata_rate_limit=overrides.pop("metadata_rate_limit", "100/hour"),
        redirect_rate_limit=overrides.pop("redirect_rate_limit", "1000/minute"),
        **overrides,
    )
    db = Database(config.database_path)
    db.initialize()
    return config, db, LinkService(db, config)


class LinkServiceTests(unittest.TestCase):
    def test_idempotent_create_returns_original_response(self):
        _, _, service = build_stack()
        payload = {"url": "https://example.com/a", "customCode": "abc123"}
        first_status, first = service.create_link("owner", payload, "idem-1", "req")
        second_status, second = service.create_link("owner", payload, "idem-1", "req")
        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 201)
        self.assertEqual(first, second)

    def test_idempotency_key_body_mismatch_conflicts(self):
        _, _, service = build_stack()
        service.create_link("owner", {"url": "https://example.com/a"}, "idem-1", "req")
        with self.assertRaises(Conflict):
            service.create_link("owner", {"url": "https://example.com/b"}, "idem-1", "req")

    def test_duplicate_custom_code_conflicts_cleanly(self):
        _, _, service = build_stack()
        service.create_link("owner", {"url": "https://example.com/a", "customCode": "same"}, "one", "req")
        with self.assertRaises(Conflict):
            service.create_link("owner", {"url": "https://example.com/b", "customCode": "same"}, "two", "req")

    def test_lifecycle_success_validation_activates_link(self):
        _, db, service = build_stack()
        service.create_link("owner", {"url": "https://example.com/a", "customCode": "life"}, "idem", "req")
        self.assertTrue(ValidationWorker(db, build_stack()[0], SuccessValidator()).process_one())
        link = service.get_metadata("owner", "life")
        self.assertEqual(link["status"], "active")
        self.assertEqual(link["metadata"]["title"], "Example")

    def test_transient_validation_retries_then_dead_without_losing_link(self):
        config, db, service = build_stack(validation_max_attempts=1)
        service.create_link("owner", {"url": "https://transient.example.com/a", "customCode": "retry"}, "idem", "req")
        self.assertTrue(ValidationWorker(db, config, AlwaysTransientValidator()).process_one())
        link = service.get_metadata("owner", "retry")
        self.assertEqual(link["status"], "pending")
        self.assertEqual(link["validationError"], "provider unavailable")
        with db.connect() as conn:
            job = conn.execute("SELECT status FROM validation_jobs").fetchone()
        self.assertEqual(job["status"], "dead")

    def test_redirect_respects_lifecycle(self):
        config, db, service = build_stack()
        service.create_link("owner", {"url": "https://example.com/a", "customCode": "wait"}, "idem", "req")
        with self.assertRaises(Conflict):
            service.redirect("wait", "req")
        ValidationWorker(db, config, SuccessValidator()).process_one()
        self.assertEqual(service.redirect("wait", "req"), "https://example.com/a")
        service.disable_link("owner", "wait", "req")
        with self.assertRaises(Conflict):
            service.redirect("wait", "req")

    def test_usage_limit_is_atomic_under_concurrent_redirects(self):
        config, db, service = build_stack()
        service.create_link("owner", {"url": "https://example.com/a", "customCode": "limit", "usageLimit": 5}, "idem", "req")
        ValidationWorker(db, config, SuccessValidator()).process_one()

        def visit():
            try:
                service.redirect("limit", "req")
                return True
            except Conflict:
                return False

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _: visit(), range(30)))
        self.assertEqual(sum(results), 5)
        self.assertEqual(service.get_metadata("owner", "limit")["usageCount"], 5)

    def test_concurrent_same_custom_code_creates_one_link(self):
        _, _, service = build_stack()

        def create(i):
            try:
                service.create_link("owner", {"url": f"https://example.com/{i}", "customCode": "race"}, f"idem-{i}", "req")
                return True
            except Conflict:
                return False

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(create, range(20)))
        self.assertEqual(sum(results), 1)

    def test_rate_limit_is_concurrent_safe(self):
        config, db, _ = build_stack()
        limiter = RateLimiter(db)

        def attempt():
            try:
                limiter.check("create:owner", "5/hour")
                return True
            except RateLimited:
                return False

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _: attempt(), range(20)))
        self.assertEqual(sum(results), 5)

    def test_production_config_can_select_postgres_and_redis(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_BACKEND": "postgres",
                "DATABASE_URL": "postgresql://user:pass@example.com:5432/app",
                "REDIS_URL": "rediss://redis.example.com:6379/0",
                "RATE_LIMIT_BACKEND": "redis",
                "API_KEYS": "one,two",
            },
            clear=False,
        ):
            config = Config.from_env()
        self.assertEqual(config.database_backend, "postgres")
        self.assertEqual(config.rate_limit_backend, "redis")
        self.assertEqual(config.api_keys, ("one", "two"))

    def test_postgres_backend_requires_database_url(self):
        config = Config(database_backend="postgres", database_url=None)
        with self.assertRaises(RuntimeError):
            create_database(config)

    def test_postgres_sql_helpers_translate_sqlite_placeholders(self):
        self.assertEqual(_pg_sql("SELECT * FROM links WHERE id = ?"), "SELECT * FROM links WHERE id = %s")
        self.assertEqual(_split_sql_statements("SELECT 1; SELECT 2;"), ["SELECT 1", "SELECT 2"])


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.config, self.db, self.service = build_stack(create_rate_limit="1/hour")
        self.server = ShortenerServer(("127.0.0.1", 0), self.db, self.config, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.base + path, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None

    def test_protected_create_requires_authentication(self):
        with self.assertRaises(HTTPError) as ctx:
            self.request("POST", "/api/v1/links", {"url": "https://example.com"})
        self.assertEqual(ctx.exception.code, 401)

    def test_rate_limit_returns_429(self):
        headers = {"Authorization": "Bearer secret", "Idempotency-Key": "one"}
        status, _ = self.request("POST", "/api/v1/links", {"url": "https://example.com/a"}, headers)
        self.assertEqual(status, 201)
        headers = {"Authorization": "Bearer secret", "Idempotency-Key": "two"}
        with self.assertRaises(HTTPError) as ctx:
            self.request("POST", "/api/v1/links", {"url": "https://example.com/b"}, headers)
        self.assertEqual(ctx.exception.code, 429)

    def test_health_and_readiness(self):
        status, health = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        status, ready = self.request("GET", "/readyz")
        self.assertEqual(status, 200)
        self.assertEqual(ready["status"], "ready")


if __name__ == "__main__":
    unittest.main()
