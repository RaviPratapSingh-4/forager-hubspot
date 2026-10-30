"""Create the custom HubSpot properties (idempotent): python -m app.setup_properties"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import get_settings, setup_logging
from .hubspot import HubSpot

log = logging.getLogger("app")

COMPANY = {
    "forager_industry": "Forager industry", "forager_employees_range": "Forager employees range",
    "forager_linkedin_url": "Forager LinkedIn URL", "forager_operating_status": "Forager operating status",
    "forager_logo_url": "Forager logo URL", "forager_keywords": "Forager keywords",
    "forager_company_id": "Forager company ID", "forager_enriched_at": "Forager enriched at",
}
CONTACT = {
    "forager_linkedin_url": "Forager LinkedIn URL", "forager_company_linkedin_url": "Forager company LinkedIn URL",
    "forager_person_id": "Forager person ID", "forager_headline": "Forager headline",
    "forager_person_description": "Forager person description (LinkedIn About)",
    "forager_skills": "Forager skills", "forager_enriched_at": "Forager enriched at",
}
TEXTAREA = {"forager_person_description", "forager_keywords", "forager_skills"}


async def ensure(hs: HubSpot, obj: str, props: dict[str, str], fallback_group: str) -> None:
    g = await hs.req("POST", f"/crm/v3/properties/{obj}/groups", json={"name": "forager", "label": "Forager"})
    group = "forager" if g.status_code in (200, 201, 409) else fallback_group
    for name, label in props.items():
        body = {"name": name, "label": label, "type": "string", "groupName": group,
                "fieldType": "textarea" if name in TEXTAREA else "text"}
        r = await hs.req("POST", f"/crm/v3/properties/{obj}", json=body)
        if r.status_code in (200, 201):
            log.info("property created", extra={"object": obj, "prop": name})
        elif r.status_code == 409 or "already exists" in r.text.lower():
            log.info("property exists", extra={"object": obj, "prop": name})
        else:
            log.error("property failed", extra={"object": obj, "prop": name, "status": r.status_code, "body": r.text[:200]})


async def run() -> None:
    s = get_settings()
    async with httpx.AsyncClient(timeout=30) as c:
        hs = HubSpot(s, c)
        await ensure(hs, "companies", COMPANY, "companyinformation")
        await ensure(hs, "contacts", CONTACT, "contactinformation")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run())
