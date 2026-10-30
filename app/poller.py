
"""Fallback trigger: polls HubSpot for newly created companies/contacts."""
from __future__ import annotations

import asyncio
import logging
import time

from .http import check

log = logging.getLogger("app")


async def poll_loop(hs, svc, every: float) -> None:
    cursor = int((time.time() - 900) * 1000)
    log.info("poller started", extra={"interval_seconds": every})

    while True:
        started = int(time.time() * 1000)
        try:
            for kind, typ in (("companies", "company"), ("contacts", "contact")):
                body = {
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": str(cursor),
                        }]
                    }],
                    "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
                    "properties": ["createdate"],
                    "limit": 50,
                }

                r = await hs.req(
                    "POST",
                    f"/crm/v3/objects/{kind}/search",
                    json=body,
                )
                check(r)
                records = r.json().get("results", [])
                log.info(
                    "poll cycle",
                    extra={"type": typ, "records_found": len(records)},
                )

                for o in records:
                    try:
                        log.info(
                            "poll: new record",
                            extra={"type": typ, "id": o["id"]},
                        )
                        await svc.handle_event(typ, str(o["id"]))
                    except Exception:
                        log.exception(
                            "poll record failed",
                            extra={"type": typ, "id": o.get("id")},
                        )

            cursor = started - 60_000

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll failed")

        await asyncio.sleep(every)
