"""Optional Firebase persistence — archive every screening run in full.

Each saved run keeps:
  * the structured inputs and computed outputs (Firestore document),
  * the source financial statement, if one was uploaded (Cloud Storage),
  * the generated PDF tear-sheet (Cloud Storage).

Mirrors the AI-key pattern: with a Firebase service-account credential the app
gains Save/History; without it the app runs fully offline and nothing is needed
to screen a company.

Credential resolution order:
  1. a service-account JSON dict passed in (e.g. uploaded in the sidebar),
  2. the ``FIREBASE_SERVICE_ACCOUNT`` env var holding the JSON *content* (for hosts
     like Replit / Render where secrets are env-var strings, not files),
  3. a local ``firebase-service-account.json`` file (git-ignored),
  4. the ``GOOGLE_APPLICATION_CREDENTIALS`` env var pointing at a JSON file.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path

COLLECTION = "runs"
LOCAL_KEY_FILE = "firebase-service-account.json"
PROVIDER_BEDROCK = "bedrock"


class StorageError(RuntimeError):
    """Raised when a Firestore / Cloud Storage operation cannot complete."""


def available() -> bool:
    """True if the firebase-admin SDK is importable."""
    try:
        import firebase_admin  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_credentials(cred_dict: dict | None = None, *, base_dir: str | os.PathLike | None = None) -> dict | None:
    """Find a service-account credential dict, or None if none is configured."""
    if cred_dict:
        return cred_dict
    inline_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if inline_json:
        try:
            return json.loads(inline_json)
        except json.JSONDecodeError:
            return None
    root = Path(base_dir) if base_dir else Path.cwd()
    local = root / LOCAL_KEY_FILE
    if local.exists():
        return _load_json(local)
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env and Path(env).exists():
        return _load_json(Path(env))
    return None


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def default_bucket(cred_dict: dict) -> str:
    """Best-guess default Cloud Storage bucket for the project.

    Newer Firebase projects use ``<project>.firebasestorage.app``; older ones use
    ``<project>.appspot.com``. Override in the sidebar if uploads fail.
    """
    return f"{cred_dict.get('project_id', '')}.firebasestorage.app"


def _get_app(cred_dict: dict):
    import firebase_admin
    from firebase_admin import credentials

    app_name = f"sharia-scope-{cred_dict.get('project_id', 'default')}"
    try:
        return firebase_admin.get_app(app_name)
    except ValueError:
        try:
            return firebase_admin.initialize_app(credentials.Certificate(cred_dict), name=app_name)
        except Exception as exc:  # malformed key, etc.
            raise StorageError(f"Could not initialise Firebase: {exc}") from exc


def _firestore(cred_dict: dict):
    from firebase_admin import firestore

    return firestore.client(_get_app(cred_dict))


def _bucket(cred_dict: dict, bucket_name: str | None):
    from firebase_admin import storage as fb_storage

    name = bucket_name or default_bucket(cred_dict)
    return fb_storage.bucket(name, app=_get_app(cred_dict))


def _upload(cred_dict: dict, path: str, data: bytes, content_type: str, bucket_name: str | None) -> None:
    try:
        blob = _bucket(cred_dict, bucket_name).blob(path)
        blob.upload_from_string(data, content_type=content_type)
    except Exception as exc:
        raise StorageError(_friendly(exc, "upload")) from exc


def download_blob(cred_dict: dict, path: str, *, bucket_name: str | None = None) -> bytes:
    """Fetch a stored file (source statement or tear-sheet) by its path."""
    try:
        return _bucket(cred_dict, bucket_name).blob(path).download_as_bytes()
    except Exception as exc:
        raise StorageError(_friendly(exc, "download")) from exc


def save_run(
    cred_dict: dict,
    *,
    record: dict,
    source: dict | None = None,
    report_bytes: bytes | None = None,
    bucket_name: str | None = None,
    inherit_source: dict | None = None,
) -> dict:
    """Persist one full run. ``source`` is ``{"bytes": ..., "name": ...}`` or None.

    ``inherit_source`` (``{"source_path", "source_filename", "source_sha256"}``)
    lets a derived revision reference its parent's already-stored source blob
    instead of losing it. Returns ``{"id", "files_archived", "storage_note"}``.
    """
    from firebase_admin import firestore

    client = _firestore(cred_dict)
    try:
        ref = client.collection(COLLECTION).document()
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc

    run_id = ref.id
    payload = dict(record)
    # Record provenance (filename + checksum) even if the binary upload fails.
    if source and source.get("bytes"):
        payload["source_filename"] = source.get("name") or "source"
        payload["source_sha256"] = hashlib.sha256(source["bytes"]).hexdigest()
    elif inherit_source and inherit_source.get("source_path"):
        payload["source_path"] = inherit_source["source_path"]
        payload["source_filename"] = inherit_source.get("source_filename", "")
        payload["source_sha256"] = inherit_source.get("source_sha256", "")
        payload["source_inherited"] = True
    if report_bytes:
        payload["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()

    storage_note = _attach_files(cred_dict, run_id, payload, source, report_bytes, bucket_name)
    payload["files_archived"] = _is_complete(payload, source, report_bytes, inherit_source)
    # NOTE: keep `status` for the Shariah compliance state — archive completion
    # lives in its own field so the two never collide.
    payload["archive_status"] = "complete" if payload["files_archived"] else "partial"
    if storage_note:
        payload["storage_note"] = storage_note
    payload["created_at"] = firestore.SERVER_TIMESTAMP

    try:
        ref.set(payload)
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc
    return {"id": run_id, "files_archived": payload["files_archived"], "storage_note": storage_note}


def _is_complete(payload: dict, source, report_bytes, inherit_source=None) -> bool:
    """True only if every file *expected* for this run is present.

    A run with no artifacts at all is NOT complete (it's metadata-only). A source
    may be present either by upload (``source_path``) or inheritance.
    """
    expects_source = bool((source and source.get("bytes")) or (inherit_source and inherit_source.get("source_path")))
    expects_report = bool(report_bytes)
    if not (expects_source or expects_report):
        return False
    if expects_report and not payload.get("report_path"):
        return False
    if expects_source and not payload.get("source_path"):
        return False
    return True


def _attach_files(cred_dict, run_id, payload, source, report_bytes, bucket_name) -> str | None:
    """Best-effort upload of source + report; sets *_path on payload. Returns a note on failure."""
    note = None
    if source and source.get("bytes"):
        filename = _safe_name(source.get("name") or "source")
        path = f"runs/{run_id}/source_{filename}"
        try:
            _upload(cred_dict, path, source["bytes"], _guess_type(filename), bucket_name)
            payload["source_path"] = path
        except StorageError as exc:
            note = str(exc)
    if report_bytes:
        path = f"runs/{run_id}/tearsheet.pdf"
        try:
            _upload(cred_dict, path, report_bytes, "application/pdf", bucket_name)
            payload["report_path"] = path
        except StorageError as exc:
            note = str(exc)
    return note


def attach_files_to_run(
    cred_dict: dict,
    run_id: str,
    *,
    source: dict | None = None,
    report_bytes: bytes | None = None,
    bucket_name: str | None = None,
) -> dict:
    """Backfill documents into an EXISTING run (used once Storage is enabled)."""
    client = _firestore(cred_dict)
    ref = client.collection(COLLECTION).document(run_id)
    update: dict = {}
    if source and source.get("bytes"):
        update["source_filename"] = source.get("name") or "source"
        update["source_sha256"] = hashlib.sha256(source["bytes"]).hexdigest()
    if report_bytes:
        update["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    note = _attach_files(cred_dict, run_id, update, source, report_bytes, bucket_name)
    update["files_archived"] = _is_complete(update, source, report_bytes)
    update["archive_status"] = "complete" if update["files_archived"] else "partial"
    try:
        ref.set(update, merge=True)
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc
    return {"id": run_id, "files_archived": update["files_archived"], "storage_note": note}


def delete_run(cred_dict: dict, run_id: str, *, bucket_name: str | None = None) -> None:
    """Delete a run's stored files then its document.

    If a file deletion fails, the record is RETAINED (marked
    ``archive_status = deletion_failed``) so the orphan can be cleaned up later.
    """
    client = _firestore(cred_dict)
    ref = client.collection(COLLECTION).document(run_id)
    snap = ref.get()
    data = (snap.to_dict() or {}) if snap.exists else {}
    failed = False
    for key in ("source_path", "report_path"):
        path = data.get(key)
        if path:
            try:
                _bucket(cred_dict, bucket_name).blob(path).delete()
            except Exception:
                failed = True
    if failed:
        try:
            ref.set({"archive_status": "deletion_failed"}, merge=True)
        except Exception:
            pass
        raise StorageError("Could not delete one or more stored files — the record was kept for cleanup.")
    try:
        ref.delete()
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc


def bucket_exists(cred_dict: dict, bucket_name: str | None = None) -> bool:
    """True if the Cloud Storage bucket is reachable (Storage enabled)."""
    try:
        return bool(_bucket(cred_dict, bucket_name).exists())
    except Exception:
        return False


def bump_cost(cred_dict: dict, provider: str, amount: float) -> None:
    """Atomically add a run's AI cost to the persistent per-provider totals."""
    if not amount:
        return
    from firebase_admin import firestore

    field = "bedrock_usd" if provider == PROVIDER_BEDROCK else "anthropic_usd"
    try:
        _firestore(cred_dict).collection("app_meta").document("cost_totals").set(
            {field: firestore.Increment(float(amount)), "updated_at": firestore.SERVER_TIMESTAMP}, merge=True
        )
    except Exception:
        pass  # cost telemetry must never block the workflow


def read_costs(cred_dict: dict) -> dict:
    """Return persistent cost totals ``{anthropic_usd, bedrock_usd}`` (or {})."""
    try:
        snap = _firestore(cred_dict).collection("app_meta").document("cost_totals").get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception:
        return {}


def get_run(cred_dict: dict, run_id: str) -> dict | None:
    """Return a single run document, or None if it doesn't exist."""
    client = _firestore(cred_dict)
    try:
        snap = client.collection(COLLECTION).document(run_id).get()
        if not snap.exists:
            return None
        row = snap.to_dict() or {}
        row["id"] = snap.id
        return row
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc


def list_runs(cred_dict: dict, limit: int = 100) -> list[dict]:
    """Return recent saved runs (metadata only), newest first."""
    from firebase_admin import firestore

    client = _firestore(cred_dict)
    try:
        query = (
            client.collection(COLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        rows: list[dict] = []
        for doc in query.stream():
            row = doc.to_dict() or {}
            row["id"] = doc.id
            rows.append(row)
        return rows
    except Exception as exc:
        raise StorageError(_friendly(exc, "firestore")) from exc


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "file"


def _guess_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _friendly(exc: Exception, op: str) -> str:
    text = str(exc)
    low = text.lower()
    if "PERMISSION_DENIED" in text or "insufficient permissions" in low or "403" in text:
        return f"{op}: permission denied — check that Firestore/Storage are enabled and the service account has access."
    if "NOT_FOUND" in text or "does not exist" in low or "no such bucket" in low or "404" in text:
        return f"{op}: resource not found — make sure Firestore and Cloud Storage are enabled, and the Storage bucket name is correct."
    return f"{op} error: {text}"
