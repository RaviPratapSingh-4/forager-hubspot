"""Fallback trigger: polls HubSpot for newly created companies/contacts and runs the same handlers as the webhook."""
from __future__ import annotations

import asyncio
import logging
import time

from .http import check

log = logging.getLogger("app")


async def poll_loop(hs, svc, every: float) -> None:
    cursor = int((time.time() - 900) * 1000)  # look back 15 min on start; handlers are idempotent
    while True:
        started = int(time.time() * 1000)
        try:
            for kind, typ in (("companies", "company"), ("contacts", "contact")):
                body = {"filterGroups": [{"filters": [{"propertyName": "createdate", "operator": "GTE", "value": str(cursor)}]}],
                        "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
                        "properties": ["createdate"], "limit": 50}
                r = await hs.req("POST", f"/crm/v3/objects/{kind}/search", json=body)
                check(r)
                for o in r.json().get("results", []):
                    log.info("poll: new record", extra={"type": typ, "id": o["id"]})
                    await svc.handle_event(typ, str(o["id"]))
            cursor = started - 60_000  # overlap for search-index lag
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll failed")
        await asyncio.sleep(every)
