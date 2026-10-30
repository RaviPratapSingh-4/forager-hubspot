from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

log = logging.getLogger("app")
router = APIRouter()


def check_secret(request: Request) -> None:
    s = request.app.state.settings
    supplied = request.headers.get("x-webhook-secret") or request.query_params.get("secret") or ""
    if not s.webhook_secret or not hmac.compare_digest(supplied, s.webhook_secret):
        raise HTTPException(401, "unauthorized")


def _v3_ok(request: Request, body: bytes) -> bool:
    s = request.app.state.settings
    sig, ts = request.headers.get("x-hubspot-signature-v3"), request.headers.get("x-hubspot-request-timestamp")
    if not (sig and ts and s.hubspot_client_secret and s.public_base_url):
        return False
    try:
        if abs(time.time() * 1000 - int(ts)) > 300_000:
            return False
    except ValueError:
        return False
    uri = s.public_base_url.rstrip("/") + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    raw = (request.method + uri + body.decode() + ts).encode()
    digest = base64.b64encode(hmac.new(s.hubspot_client_secret.encode(), raw, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, sig)


def _kind(v: str) -> str:
    v = (v or "").lower()
    return "company" if v.startswith(("company", "companies", "0-2")) else "contact" if v.startswith(("contact", "0-1")) else ""


def extract_events(payload, query_type: str | None) -> list[tuple[str, str]]:
    """Accepts a workflow webhook body or a list of app-subscription events. Creation events only."""
    items = payload if isinstance(payload, list) else [payload]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sub = it.get("subscriptionType") or ""
        if sub and not sub.endswith(".creation"):
            continue
        obj = it.get("object") if isinstance(it.get("object"), dict) else {}
        props = obj.get("properties") or it.get("properties") or {}
        typ = _kind(query_type or "") or _kind(sub) or _kind(str(obj.get("objectType") or it.get("objectType") or ""))
        hs_id = props.get("hs_object_id")
        hs_id = hs_id.get("value") if isinstance(hs_id, dict) else hs_id
        oid = it.get("objectId") or obj.get("objectId") or it.get("hs_object_id") or hs_id
        if typ and oid:
            out.append((typ, str(oid)))
    return out


@router.post("/webhooks/hubspot")
async def hubspot_webhook(request: Request, bg: BackgroundTasks):
    body = await request.body()
    try:
        check_secret(request)
    except HTTPException:
        if not _v3_ok(request, body):
            raise
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "invalid json")
    events = extract_events(payload, request.query_params.get("type"))
    log.info("webhook received", extra={"events": events})
    for typ, oid in events:
        bg.add_task(request.app.state.svc.handle_event, typ, oid)
    return {"accepted": [{"type": t, "id": i} for t, i in events]}
