import json
import logging
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .auth import Authenticator
from .errors import AppError, BadRequest, DependencyUnavailable
from .logging_utils import log_event
from .rate_limit import create_rate_limiter

LOGGER = logging.getLogger("shortener.http")


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ShortenerHTTP/0.1"

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, format, *args):
        return

    def _handle(self, method: str):
        started = time.perf_counter()
        request_id = self.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex}"
        status = 500
        try:
            status = self._route(method, request_id)
        except AppError as exc:
            status = exc.status_code
            self._send_error(exc.status_code, exc.code, exc.message, request_id)
        except Exception:
            status = 500
            LOGGER.exception("unhandled_error", extra={"fields": {"requestId": request_id}})
            self._send_error(500, "internal_error", "An unexpected error occurred.", request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                LOGGER,
                logging.INFO,
                "http.request",
                requestId=request_id,
                method=method,
                path=urlparse(self.path).path,
                statusCode=status,
                durationMs=duration_ms,
            )

    def _route(self, method: str, request_id: str) -> int:
        path = urlparse(self.path).path
        if method == "GET" and path == "/healthz":
            self._send_json(200, {"status": "ok"}, request_id)
            return 200
        if method == "GET" and path == "/readyz":
            if not self.server.db.ready():
                raise DependencyUnavailable()
            if hasattr(self.server.rate_limiter, "ready") and not self.server.rate_limiter.ready():
                raise DependencyUnavailable("Redis is unavailable.", code="redis_unavailable")
            self._send_json(200, {"status": "ready"}, request_id)
            return 200
        if method == "GET" and path == "/metrics":
            self._send_text(200, self.server.metrics_snapshot(), request_id, "text/plain; charset=utf-8")
            return 200
        if method == "POST" and path == "/api/v1/links":
            owner_id = self._authenticate()
            self.server.rate_limiter.check(f"create:{owner_id}", self.server.config.create_rate_limit)
            body = self._read_json()
            status, response = self.server.service.create_link(
                owner_id,
                body,
                self.headers.get("Idempotency-Key"),
                request_id,
            )
            self._send_json(status, response, request_id)
            return status
        if path.startswith("/api/v1/links/"):
            owner_id = self._authenticate()
            self.server.rate_limiter.check(f"metadata:{owner_id}", self.server.config.metadata_rate_limit)
            suffix = path.removeprefix("/api/v1/links/").strip("/")
            parts = suffix.split("/")
            short_code = unquote(parts[0])
            if method == "GET" and len(parts) == 1:
                self._send_json(200, self.server.service.get_metadata(owner_id, short_code), request_id)
                return 200
            if method == "POST" and len(parts) == 2 and parts[1] == "disable":
                self._send_json(200, self.server.service.disable_link(owner_id, short_code, request_id), request_id)
                return 200
        if method == "GET" and path.count("/") == 1 and path != "/":
            client_ip = self.client_address[0] if self.client_address else "unknown"
            self.server.rate_limiter.check(f"redirect:{client_ip}", self.server.config.redirect_rate_limit)
            short_code = unquote(path.lstrip("/"))
            destination = self.server.service.redirect(short_code, request_id)
            self.send_response(302)
            self.send_header("Location", destination)
            self.send_header("X-Request-Id", request_id)
            self.end_headers()
            return 302
        raise BadRequest("Unsupported route or method.", code="unsupported_route", status_code=404)

    def _authenticate(self) -> str:
        return self.server.authenticator.authenticate(self.headers.get("Authorization"))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BadRequest("Request body must be valid JSON.", code="invalid_json") from exc

    def _send_json(self, status: int, payload: dict, request_id: str):
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", request_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, payload: str, request_id: str, content_type: str):
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", request_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str, request_id: str):
        self._send_json(status, {"error": {"code": code, "message": message, "requestId": request_id}}, request_id)


class ShortenerServer(ThreadingHTTPServer):
    def __init__(self, address, db, config, service):
        super().__init__(address, ApiHandler)
        self.db = db
        self.config = config
        self.service = service
        self.authenticator = Authenticator(config.api_keys)
        self.rate_limiter = create_rate_limiter(config, db)

    def metrics_snapshot(self) -> str:
        with self.db.connect() as conn:
            links = conn.execute("SELECT status, COUNT(*) AS count FROM links GROUP BY status").fetchall()
            jobs = conn.execute("SELECT status, COUNT(*) AS count FROM validation_jobs GROUP BY status").fetchall()
        lines = [
            "# HELP shortener_links Number of links by status.",
            "# TYPE shortener_links gauge",
        ]
        lines.extend(f'shortener_links{{status="{row["status"]}"}} {row["count"]}' for row in links)
        lines.extend(
            [
                "# HELP shortener_validation_jobs Number of validation jobs by status.",
                "# TYPE shortener_validation_jobs gauge",
            ]
        )
        lines.extend(f'shortener_validation_jobs{{status="{row["status"]}"}} {row["count"]}' for row in jobs)
        return "\n".join(lines) + "\n"
