from __future__ import annotations

import logging
import re

import httpx

from .config import Settings
from .http import ApiError, check, send

log = logging.getLogger("app")

COMPANY_READ = ["name", "domain", "website", "forager_enriched_at"]
CONTACT_READ = ["firstname", "lastname", "email", "jobtitle", "phone", "city", "state", "country",
                "company", "website", "forager_enriched_at", "forager_linkedin_url"]


class HubSpot:
    def __init__(self, s: Settings, client: httpx.AsyncClient) -> None:
        s.need("hubspot_token")
        self.c, self.base = client, s.hubspot_base
        self.h = {"Authorization": f"Bearer {s.hubspot_token}", "Content-Type": "application/json"}

    async def req(self, method: str, path: str, **kw) -> httpx.Response:
        return await send(self.c, method, self.base + path, headers=self.h, **kw)

    async def get(self, kind: str, oid: str, props: list[str]) -> dict:
        r = await self.req("GET", f"/crm/v3/objects/{kind}/{oid}", params={"properties": ",".join(props)})
        check(r)
        return r.json()

    async def find(self, kind: str, prop: str, value: str, props: tuple[str, ...] = ("name",)) -> dict | None:
        body = {"filterGroups": [{"filters": [{"propertyName": prop, "operator": "EQ", "value": value}]}],
                "properties": list(props), "limit": 1}
        r = await self.req("POST", f"/crm/v3/objects/{kind}/search", json=body)
        check(r)
        res = r.json().get("results") or []
        return res[0] if res else None

    async def _write(self, method: str, path: str, props: dict, ok: tuple[int, ...]) -> httpx.Response:
        """Write; if HubSpot rejects named properties (missing / invalid option), drop them and retry."""
        props = dict(props)
        r = None
        for _ in range(6):
            r = await self.req(method, path, json={"properties": props})
            if r.status_code in ok or r.status_code != 400:
                return r
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = r.text
            bad = (set(re.findall(r'"name"\s*:\s*"([^"]+)"', msg))
                   | set(re.findall(r'Property "([^"]+)" does not exist', msg))) & set(props)
            if not bad:
                return r
            log.warning("hubspot rejected properties; dropping", extra={"props": sorted(bad)})
            for k in bad:
                props.pop(k)
        return r  # type: ignore[return-value]

    async def create(self, kind: str, props: dict) -> dict:
        r = await self._write("POST", f"/crm/v3/objects/{kind}", props, (200, 201))
        if r.status_code == 409:
            m = re.search(r"Existing ID:\s*(\d+)", r.text)
            if m:
                return {"id": m.group(1), "existing": True}
        check(r)
        return r.json()

    async def update(self, kind: str, oid: str, props: dict) -> dict:
        r = await self._write("PATCH", f"/crm/v3/objects/{kind}/{oid}", props, (200,))
        check(r)
        return r.json()

    async def associate(self, contact_id: str, company_id: str) -> None:
        r = await self.req("PUT", f"/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}")
        check(r)

    async def company_contact_ids(self, company_id: str) -> list[str]:
        r = await self.req("GET", f"/crm/v4/objects/companies/{company_id}/associations/contacts")
        check(r)
        return [str(x["toObjectId"]) for x in r.json().get("results", [])]

    async def contact_company_ids(self, contact_id: str) -> list[str]:
        r = await self.req("GET", f"/crm/v4/objects/contacts/{contact_id}/associations/companies")
        check(r)
        return [str(x["toObjectId"]) for x in r.json().get("results", [])]
