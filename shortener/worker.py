import json
import logging
import time

from .logging_utils import log_event
from .service import iso_now
from .validation import PermanentValidationError, TransientValidationError, UrlValidator

LOGGER = logging.getLogger("shortener.worker")


class ValidationWorker:
    def __init__(self, db, config, validator=None):
        self.db = db
        self.config = config
        self.validator = validator or UrlValidator(enable_network_checks=False)

    def process_one(self, request_id: str = "worker") -> bool:
        now = iso_now()
        with self.db.transaction() as conn:
            job = conn.execute(
                """
                SELECT validation_jobs.*, links.destination_url
                FROM validation_jobs
                JOIN links ON links.id = validation_jobs.link_id
                WHERE validation_jobs.status IN ('queued', 'retrying')
                  AND validation_jobs.next_run_at <= ?
                ORDER BY validation_jobs.created_at
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not job:
                return False
            conn.execute(
                "UPDATE validation_jobs SET status = 'processing', updated_at = ? WHERE id = ?",
                (now, job["id"]),
            )

        try:
            result = self.validator.validate(job["destination_url"])
            with self.db.transaction() as conn:
                link = conn.execute("SELECT metadata FROM links WHERE id = ?", (job["link_id"],)).fetchone()
                metadata = json.loads(link["metadata"] or "{}") if link else {}
                metadata.update(result.metadata)
                conn.execute(
                    """
                    UPDATE links
                    SET status = 'active', validation_error = NULL, metadata = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (json.dumps(metadata, sort_keys=True), iso_now(), job["link_id"]),
                )
                conn.execute(
                    "UPDATE validation_jobs SET status = 'succeeded', updated_at = ? WHERE id = ?",
                    (iso_now(), job["id"]),
                )
            log_event(LOGGER, logging.INFO, "link.validation.succeeded", requestId=request_id, linkId=job["link_id"])
            return True
        except PermanentValidationError as exc:
            safe_error = str(exc)
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE links SET status = 'failed', validation_error = ?, updated_at = ? WHERE id = ?",
                    (safe_error, iso_now(), job["link_id"]),
                )
                conn.execute(
                    """
                    UPDATE validation_jobs
                    SET status = 'failed', attempt_count = attempt_count + 1, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (safe_error, iso_now(), job["id"]),
                )
            log_event(LOGGER, logging.WARNING, "link.validation.failed", requestId=request_id, linkId=job["link_id"], reason="permanent")
            return True
        except TransientValidationError as exc:
            self._retry_or_dead(job, str(exc), request_id)
            return True
        except Exception as exc:
            self._retry_or_dead(job, "Unexpected validation failure.", request_id)
            log_event(LOGGER, logging.ERROR, "background_job.failed", requestId=request_id, linkId=job["link_id"], errorType=type(exc).__name__)
            return True

    def _retry_or_dead(self, job, safe_error: str, request_id: str):
        next_attempt = int(job["attempt_count"]) + 1
        now = iso_now()
        if next_attempt >= self.config.validation_max_attempts:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE validation_jobs
                    SET status = 'dead', attempt_count = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_attempt, safe_error, now, job["id"]),
                )
                conn.execute(
                    "UPDATE links SET validation_error = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                    (safe_error, now, job["link_id"]),
                )
            log_event(LOGGER, logging.ERROR, "background_job.failed", requestId=request_id, linkId=job["link_id"], reason="max_attempts")
            return

        delay_seconds = min(60, 2 ** next_attempt)
        next_run = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay_seconds))
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE validation_jobs
                SET status = 'retrying', attempt_count = ?, next_run_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_attempt, next_run, safe_error, now, job["id"]),
            )
            conn.execute(
                "UPDATE links SET validation_error = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                (safe_error, now, job["link_id"]),
            )
        log_event(LOGGER, logging.WARNING, "background_job.retrying", requestId=request_id, linkId=job["link_id"], attempt=next_attempt)

    def run_forever(self):
        while True:
            processed = self.process_one()
            if not processed:
                time.sleep(1)

