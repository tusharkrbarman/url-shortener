import hashlib
import json
import logging
import re
import secrets
import string
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .errors import BadRequest, Conflict, Forbidden, Gone, NotFound
from .logging_utils import log_event

LOGGER = logging.getLogger("shortener.service")
CODE_ALPHABET = string.ascii_letters + string.digits
CUSTOM_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None):
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BadRequest("expiresAt must be an ISO-8601 timestamp.", code="invalid_expiration") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_request(payload: dict) -> str:
    allowed = {
        "url": payload.get("url"),
        "customCode": payload.get("customCode"),
        "usageLimit": payload.get("usageLimit"),
        "expiresAt": payload.get("expiresAt"),
        "metadata": payload.get("metadata") or {},
    }
    return json_dumps(allowed)


def request_hash(payload: dict) -> str:
    return hashlib.sha256(normalize_request(payload).encode("utf-8")).hexdigest()


def row_to_link(row) -> dict:
    return {
        "id": row["id"],
        "ownerId": row["owner_id"],
        "shortCode": row["short_code"],
        "destinationUrl": row["destination_url"],
        "status": row["status"],
        "usageCount": row["usage_count"],
        "usageLimit": row["usage_limit"],
        "expiresAt": row["expires_at"],
        "validationError": row["validation_error"],
        "metadata": json.loads(row["metadata"] or "{}"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class LinkService:
    def __init__(self, db, config):
        self.db = db
        self.config = config

    def create_link(self, owner_id: str, payload: dict, idempotency_key: str | None, request_id: str) -> tuple[int, dict]:
        if not idempotency_key:
            raise BadRequest("Idempotency-Key header is required.", code="missing_idempotency_key")
        validated = self._validate_create_payload(payload)
        req_hash = request_hash(payload)
        now = iso_now()
        expires = (utc_now() + timedelta(hours=self.config.idempotency_ttl_hours)).isoformat().replace("+00:00", "Z")

        with self.db.transaction() as conn:
            conn.execute("DELETE FROM idempotency_keys WHERE expires_at <= ?", (now,))
            existing = conn.execute(
                "SELECT request_hash, response_body, status_code FROM idempotency_keys WHERE owner_id = ? AND key = ?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != req_hash:
                    raise Conflict("Idempotency key was already used with a different request.", code="idempotency_conflict")
                if existing["response_body"] is None:
                    raise Conflict("Original idempotent request is still processing.", code="idempotency_in_progress")
                return int(existing["status_code"]), json.loads(existing["response_body"])

            conn.execute(
                """
                INSERT INTO idempotency_keys(id, owner_id, key, request_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), owner_id, idempotency_key, req_hash, now, expires),
            )

            short_code = validated["customCode"] or self._reserve_generated_code(conn)
            link_id = str(uuid.uuid4())
            response = None
            try:
                conn.execute(
                    """
                    INSERT INTO links(
                        id, owner_id, short_code, destination_url, status,
                        usage_count, usage_limit, expires_at, metadata, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                    """,
                    (
                        link_id,
                        owner_id,
                        short_code,
                        validated["url"],
                        "pending" if self.config.validation_enabled else "active",
                        validated["usageLimit"],
                        validated["expiresAt"],
                        json_dumps(validated["metadata"]),
                        now,
                        now,
                    ),
                )
            except Exception as exc:
                if "UNIQUE constraint failed: links.short_code" in str(exc):
                    raise Conflict("Short code is already in use.", code="duplicate_short_code") from exc
                raise

            if self.config.validation_enabled:
                conn.execute(
                    """
                    INSERT INTO validation_jobs(id, link_id, status, attempt_count, next_run_at, created_at, updated_at)
                    VALUES (?, ?, 'queued', 0, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), link_id, now, now, now),
                )

            response = {
                "id": link_id,
                "shortCode": short_code,
                "shortUrl": f"{self.config.base_url}/{short_code}",
                "status": "pending" if self.config.validation_enabled else "active",
                "usageCount": 0,
                "usageLimit": validated["usageLimit"],
                "expiresAt": validated["expiresAt"],
                "createdAt": now,
            }
            conn.execute(
                "UPDATE idempotency_keys SET response_body = ?, status_code = ? WHERE owner_id = ? AND key = ?",
                (json_dumps(response), 201, owner_id, idempotency_key),
            )

        log_event(LOGGER, logging.INFO, "link.created", requestId=request_id, ownerId=owner_id, linkId=link_id, shortCode=short_code)
        return 201, response

    def get_metadata(self, owner_id: str, short_code: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM links WHERE short_code = ?", (short_code,)).fetchone()
        if not row:
            raise NotFound("Link was not found.", code="link_not_found")
        if row["owner_id"] != owner_id:
            raise Forbidden()
        return row_to_link(row)

    def disable_link(self, owner_id: str, short_code: str, request_id: str) -> dict:
        now = iso_now()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM links WHERE short_code = ?", (short_code,)).fetchone()
            if not row:
                raise NotFound("Link was not found.", code="link_not_found")
            if row["owner_id"] != owner_id:
                raise Forbidden()
            conn.execute(
                "UPDATE links SET status = 'disabled', updated_at = ? WHERE short_code = ?",
                (now, short_code),
            )
            updated = conn.execute("SELECT * FROM links WHERE short_code = ?", (short_code,)).fetchone()
        log_event(LOGGER, logging.INFO, "link.disabled", requestId=request_id, ownerId=owner_id, shortCode=short_code)
        return row_to_link(updated)

    def redirect(self, short_code: str, request_id: str) -> str:
        now = iso_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                """
                UPDATE links
                SET usage_count = usage_count + 1, updated_at = ?
                WHERE short_code = ?
                  AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (usage_limit IS NULL OR usage_count < usage_limit)
                RETURNING destination_url, usage_count, usage_limit
                """,
                (now, short_code, now),
            ).fetchone()
            if row:
                log_event(LOGGER, logging.INFO, "link.redirect.succeeded", requestId=request_id, shortCode=short_code, usageCount=row["usage_count"])
                return row["destination_url"]
            link = conn.execute("SELECT * FROM links WHERE short_code = ?", (short_code,)).fetchone()

        if not link:
            log_event(LOGGER, logging.INFO, "link.redirect.rejected", requestId=request_id, shortCode=short_code, reason="not_found")
            raise NotFound("Short link was not found.", code="link_not_found")
        if link["expires_at"] and link["expires_at"] <= now:
            raise Gone("Short link has expired.", code="link_expired")
        if link["status"] != "active":
            raise Conflict(f"Short link is {link['status']}.", code=f"link_{link['status']}")
        raise Conflict("Short link usage limit has been reached.", code="usage_limit_reached")

    def _validate_create_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise BadRequest("Request body must be a JSON object.", code="invalid_body")
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise BadRequest("url is required.", code="missing_url")
        url = url.strip()
        if len(url) > self.config.max_url_length:
            raise BadRequest("url is too long.", code="url_too_long")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BadRequest("url must be an absolute http or https URL.", code="invalid_url")

        custom_code = payload.get("customCode")
        if custom_code is not None:
            if not isinstance(custom_code, str) or not CUSTOM_CODE_RE.fullmatch(custom_code):
                raise BadRequest("customCode must match ^[A-Za-z0-9_-]{3,64}$.", code="invalid_custom_code")

        usage_limit = payload.get("usageLimit")
        if usage_limit is not None:
            if not isinstance(usage_limit, int) or usage_limit <= 0:
                raise BadRequest("usageLimit must be a positive integer.", code="invalid_usage_limit")

        expires_at = payload.get("expiresAt")
        parsed_expiration = parse_datetime(expires_at)
        if parsed_expiration and parsed_expiration <= utc_now():
            raise BadRequest("expiresAt must be in the future.", code="invalid_expiration")

        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise BadRequest("metadata must be an object.", code="invalid_metadata")
        if len(json_dumps(metadata).encode("utf-8")) > self.config.max_metadata_bytes:
            raise BadRequest("metadata is too large.", code="metadata_too_large")

        return {
            "url": url,
            "customCode": custom_code,
            "usageLimit": usage_limit,
            "expiresAt": parsed_expiration.isoformat().replace("+00:00", "Z") if parsed_expiration else None,
            "metadata": metadata,
        }

    def _reserve_generated_code(self, conn) -> str:
        for _ in range(10):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(7))
            exists = conn.execute("SELECT 1 FROM links WHERE short_code = ?", (code,)).fetchone()
            if not exists:
                return code
        raise Conflict("Could not allocate a unique short code.", code="short_code_generation_failed")

