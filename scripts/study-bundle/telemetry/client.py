"""Local event log, and an idempotent uploader for it.

The design constraint is that a session cannot be re-run. So the local
append-only log is the record of truth and the upload is a copy: writing an
event never depends on the network, never blocks the participant, and never
fails in a way that loses anything. If the whole upload path is broken for two
hours, the log on disk is still complete and can be collected by hand.

Every event carries an id derived from its own contents, and the uploader keeps
a ledger of what has landed, so running `study-sync` five times uploads each
event once. Firestore's rules only allow creating an event, never updating one,
which means a duplicate is rejected by the server as well as by the ledger --
two independent guards, because the expensive failure here is silent
double-counting in the analysis.

Standard library only. The bundle must install on a machine with nothing on it.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable

PROJECT_ID = os.environ.get("STUDY_PROJECT_ID", "sem-git")
API_KEY = "AIzaSyDsFEnfbmk2Muj1amaYVvIsajEQM8OukNY"

IDENTITY = "https://identitytoolkit.googleapis.com/v1"

BATCH = 200
TIMEOUT = 25


def emulator_host() -> str | None:
    """Where this bundle should send its data, if not the real study.

    Read from the environment first, and then from the bundle's own
    `study.json`. The second source is the one that matters: a rehearsal
    participant follows the same printed instructions as a real one --
    `bash install/setup.sh <code>` -- and has no reason to know an environment
    variable exists. A rehearsal bundle that only works when the person running
    it already knows the trick is not a rehearsal of anything.

    Without this, a rehearsal bundle talks to the real project, finds no
    participant with that code, and tells the person their code is wrong. They
    then go and check the one thing that was never the problem.
    """
    for name in ("FIRESTORE_EMULATOR_HOST", "STUDY_FIRESTORE_HOST"):
        value = os.environ.get(name)
        if value:
            return value
    return study_meta().get("firestoreHost") or None


def firestore_base() -> str:
    host = emulator_host()
    if host:
        return f"http://{host}/v1/projects/{PROJECT_ID}/databases/(default)/documents"
    return (
        f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
        "/databases/(default)/documents"
    )


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def study_home() -> Path:
    env = os.environ.get("STUDY_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def telemetry_dir() -> Path:
    d = study_home() / "telemetry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return telemetry_dir() / "events.jsonl"


def ledger_path() -> Path:
    return telemetry_dir() / "uploaded.txt"


def state_path() -> Path:
    return telemetry_dir() / "state.json"


def read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text())
    except Exception:
        return {}


def write_state(patch: dict[str, Any]) -> dict[str, Any]:
    state = read_state()
    state.update(patch)
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_path())
    return state


def study_meta() -> dict[str, Any]:
    """What this bundle is.

    The bundle knows its condition and project, because those are baked in when
    it is built. It does not know which half it is, because that depends on the
    participant: the same sgt/coursecraft bundle is somebody's first half and
    somebody else's second. Provisioning looks up the assignment and records the
    half here, which is why the two sources are merged rather than one file
    holding everything.
    """
    meta: dict[str, Any] = {}
    try:
        meta.update(json.loads((study_home() / "study.json").read_text()))
    except Exception:
        pass
    state = read_state()
    if state.get("half") is not None:
        meta["half"] = state["half"]
    return meta


def device_id() -> str:
    state = read_state()
    if "deviceId" in state:
        return str(state["deviceId"])
    new = uuid.uuid4().hex[:16]
    write_state({"deviceId": new})
    return new


def participant_code() -> str | None:
    code = os.environ.get("STUDY_CODE")
    if code:
        return code
    return read_state().get("code")


# --------------------------------------------------------------------------
# Appending
# --------------------------------------------------------------------------


def event_id(payload: dict[str, Any]) -> str:
    """Content-addressed, so the same event written twice is one event."""
    basis = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(basis).hexdigest()[:24]


def append(kind: str, **fields: Any) -> str:
    """Write one event to the local log. Never raises, never blocks."""
    meta = study_meta()
    payload: dict[str, Any] = {
        "kind": kind,
        "ts": int(time.time() * 1000),
        "half": meta.get("half"),
        "condition": meta.get("condition"),
        "project": meta.get("project"),
        "deviceId": device_id(),
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    payload["id"] = event_id(payload)

    try:
        path = log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # pragma: no cover - never break the session
        print(f"[study] could not write telemetry: {exc}", file=sys.stderr)
    return str(payload["id"])


def read_log() -> list[dict[str, Any]]:
    path = log_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn last line from a hard kill. Skip it and keep the rest;
            # dropping one event is better than refusing to upload the session.
            continue
    return out


def read_ledger() -> set[str]:
    path = ledger_path()
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def extend_ledger(ids: Iterable[str]) -> None:
    with ledger_path().open("a", encoding="utf-8") as handle:
        for i in ids:
            handle.write(f"{i}\n")


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


class UploadError(RuntimeError):
    pass


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _post(url: str, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise UploadError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise UploadError(str(exc)) from exc


def _get(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise UploadError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise UploadError(str(exc)) from exc


def sign_in() -> str:
    """Anonymous token, cached until it expires."""
    if emulator_host():
        return "owner"
    state = read_state()
    token = state.get("idToken")
    expires = float(state.get("tokenExpires") or 0)
    if token and expires > time.time() + 120:
        return str(token)

    refresh = state.get("refreshToken")
    if refresh:
        try:
            out = _post(
                f"https://securetoken.googleapis.com/v1/token?key={API_KEY}",
                {"grant_type": "refresh_token", "refresh_token": refresh},
            )
            write_state(
                {
                    "idToken": out["id_token"],
                    "refreshToken": out["refresh_token"],
                    "tokenExpires": time.time() + int(out.get("expires_in", 3600)),
                }
            )
            return str(out["id_token"])
        except UploadError:
            pass  # fall through to a fresh anonymous account

    out = _post(f"{IDENTITY}/accounts:signUp?key={API_KEY}", {"returnSecureToken": True})
    write_state(
        {
            "idToken": out["idToken"],
            "refreshToken": out["refreshToken"],
            "tokenExpires": time.time() + int(out.get("expiresIn", 3600)),
            "uid": out.get("localId"),
        }
    )
    return str(out["idToken"])


# --------------------------------------------------------------------------
# Firestore value encoding
# --------------------------------------------------------------------------


def _encode(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_encode(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(k): _encode(v) for k, v in value.items()}}}
    return {"stringValue": str(value)}


def _decode(value: dict[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "arrayValue" in value:
        return [_decode(v) for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return {k: _decode(v) for k, v in value["mapValue"].get("fields", {}).items()}
    return None


def decode_document(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _decode(v) for k, v in (doc.get("fields") or {}).items()}


# --------------------------------------------------------------------------
# Reads and writes
# --------------------------------------------------------------------------


def fetch_document(path: str) -> dict[str, Any] | None:
    token = sign_in()
    try:
        return decode_document(_get(f"{firestore_base()}/{path}", token))
    except UploadError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def set_document(path: str, fields: dict[str, Any], merge: bool = True) -> None:
    token = sign_in()
    url = f"{firestore_base()}/{path}"
    if merge:
        mask = "&".join(f"updateMask.fieldPaths={k}" for k in fields)
        url = f"{url}?{mask}"
    data = json.dumps({"fields": {k: _encode(v) for k, v in fields.items()}}).encode()
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise UploadError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise UploadError(str(exc)) from exc


# Fields the web app's schema knows about. Anything else on an event travels in
# `extra`, so an event written by a newer shim than the dashboard expects still
# lands rather than being rejected by the size rule.
KNOWN_FIELDS = {
    "id",
    "kind",
    "ts",
    "half",
    "condition",
    "requestId",
    "name",
    "text",
    "paths",
    "exitCode",
    "durationMs",
    "ok",
    "sessionId",
    "deviceId",
    "extra",
}


def _event_fields(event: dict[str, Any]) -> dict[str, Any]:
    fields = {k: v for k, v in event.items() if k in KNOWN_FIELDS}
    extra = {k: v for k, v in event.items() if k not in KNOWN_FIELDS}
    if extra:
        fields["extra"] = {**(fields.get("extra") or {}), **extra}
    fields.setdefault("name", None)
    fields.setdefault("text", None)
    return fields


def _commit(writes: list[dict[str, Any]], token: str) -> None:
    _post(f"{firestore_base()}:commit", {"writes": writes}, token)


def upload(code: str, events: list[dict[str, Any]], verbose: bool = False) -> tuple[int, int]:
    """Upload events that have not landed yet. Returns (sent, skipped)."""
    if not events:
        return (0, 0)
    token = sign_in()
    sent = 0
    skipped = 0

    for start in range(0, len(events), BATCH):
        chunk = events[start : start + BATCH]
        writes = [
            {
                "update": {
                    "name": f"projects/{PROJECT_ID}/databases/(default)/documents/participants/{code}/events/{e['id']}",
                    "fields": {k: _encode(v) for k, v in _event_fields(e).items()},
                },
                "currentDocument": {"exists": False},
            }
            for e in chunk
        ]
        try:
            _commit(writes, token)
            sent += len(chunk)
        except UploadError as exc:
            # A commit is atomic, so one already-present event fails the whole
            # batch. Fall back to one write at a time and treat "already there"
            # as success, which is exactly what it is.
            if verbose:
                print(f"[study] batch retry: {exc}", file=sys.stderr)
            for one, write in zip(chunk, writes):
                try:
                    _commit([write], token)
                    sent += 1
                except UploadError as inner:
                    text = str(inner)
                    if "ALREADY_EXISTS" in text or "already exists" in text:
                        skipped += 1
                    elif "PERMISSION_DENIED" in text and "exists" in text.lower():
                        skipped += 1
                    else:
                        raise UploadError(f"event {one.get('id')}: {text}") from inner
    return (sent, skipped)


def heartbeat(code: str, checks: dict[str, Any] | None = None, uploaded: int | None = None) -> None:
    meta = study_meta()
    fields: dict[str, Any] = {
        "deviceId": device_id(),
        "half": meta.get("half"),
        "condition": meta.get("condition"),
        "project": meta.get("project"),
        "os": f"{os.uname().sysname} {os.uname().release}" if hasattr(os, "uname") else sys.platform,
        "toolBuild": meta.get("toolBuild"),
        "bundleVersion": meta.get("bundleVersion"),
        "lastSeenAt": int(time.time() * 1000),
    }
    state = read_state()
    if "firstSeenAt" not in state:
        state = write_state({"firstSeenAt": int(time.time() * 1000)})
    fields["firstSeenAt"] = state["firstSeenAt"]
    if checks is not None:
        fields["checks"] = checks
    if uploaded is not None:
        fields["eventsUploaded"] = uploaded
    set_document(f"participants/{code}/devices/{device_id()}", fields)


def sync(code: str, verbose: bool = False) -> tuple[int, int, int]:
    """Push anything the ledger has not seen. Returns (sent, skipped, pending)."""
    events = read_log()
    done = read_ledger()
    pending = [e for e in events if e.get("id") and e["id"] not in done]
    if not pending:
        return (0, 0, 0)
    sent, skipped = upload(code, pending, verbose=verbose)
    extend_ledger(e["id"] for e in pending)
    return (sent, skipped, len(pending))
