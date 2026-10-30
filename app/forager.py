from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any
from urllib.parse import urljoin

import httpx

from .cache import Cache
from .config import Settings
from .http import ApiError, check, retry_after, send
from .mapping import e164, norm_domain

log = logging.getLogger("app")
BAD_VERDICTS = {"invalid", "undeliverable", "bounce", "bounced", "rejected"}


def pick_email(emails: list | None) -> str | None:
    for e in emails or []:
        if isinstance(e, str):
            return e.lower()
        if isinstance(e, dict):
            addr = e.get("email") or e.get("address")
            verdict = str(e.get("validation_status") or e.get("verdict") or e.get("status") or "").lower()
            if addr and verdict not in BAD_VERDICTS:
                return str(addr).lower()
    return None


class Forager:
    def __init__(self, s: Settings, client: httpx.AsyncClient, cache: Cache) -> None:
        s.need("forager_api_key")
        self.s, self.c, self.cache = s, client, cache
        self.h = {"X-API-KEY": s.forager_api_key, "Content-Type": "application/json"}
        self.v1 = f"{s.forager_base}/api/{s.forager_account_id}/datastorage/"
        self.v2 = f"{s.forager_base}/api/v2/{s.forager_account_id}/datastorage/"
        self.stats: Counter = Counter()  # live (uncached) calls per endpoint
        self.sem = asyncio.Semaphore(3)
        self.no_credits = False  # set on first 402; stops all further live calls

    def _guard(self, r: httpx.Response | None = None) -> None:
        if r is not None and r.status_code == 402:
            self.no_credits = True
            log.error("Forager credits exhausted (402); no more live calls this run")
        if self.no_credits:
            raise ApiError(402, "forager credits exhausted", "")

    def _live(self, name: str) -> None:
        self.stats[name] += 1
        cost = self.s.costs.get(name)
        log.info("forager LIVE call", extra={"endpoint": name, "credits": cost if cost is not None else "see dashboard"})

    def report(self) -> str:
        total = sum(self.stats.values())
        known = sum(self.s.costs.get(k, 0) * n for k, n in self.stats.items())
        lines = [f"  {k}: {n} live call(s)" for k, n in self.stats.items()]
        return "Forager live calls: %d (cached responses are free)\n%s\n  upper-bound credits from FORAGER_COSTS: %s (lookups bill only when found)" % (
            total, "\n".join(lines), known if self.s.costs else "n/a - set FORAGER_COSTS")

    async def _post(self, name: str, path: str, body: dict) -> Any | None:
        key = self.cache.key(name, body)
        hit = self.cache.get(key)
        if hit is not None:
            return None if isinstance(hit, dict) and hit.get("_not_found") else hit
        async with self.sem:
            self._guard()
            self._live(name)
            r = await send(self.c, "POST", self.v1 + path, headers=self.h, json=body)
        self._guard(r)
        if r.status_code in (204, 404):
            self.cache.set(key, {"_not_found": True})
            return None
        check(r)
        data = r.json()
        self.cache.set(key, data)
        return data

    async def organization(self, domain: str, fallback_query: str | None = None) -> dict | None:
        """Pick the real company among all domain matches (domain search also returns GPTs/products)."""
        d = await self._post("organization_search", "organization_search/", {"domains": [domain]})
        label = domain.split(".")[0]
        res = [o for o in (d or {}).get("search_results", [])
               if norm_domain(o.get("domain") or o.get("website")) == domain]

        def strong(o: dict) -> bool:
            slug = ((o.get("linkedin_info") or {}).get("public_identifier") or "").lower()
            return slug == label or (o.get("name") or "").strip().lower() == label

        best = max((o for o in res if strong(o)), key=lambda o: o.get("employees_amount") or 0, default=None)
        if best:
            return best
        # search only returns a page of look-alikes: the real org is embedded in role-search results
        rs = await self.role_search(domain=domain, role_title=fallback_query)
        orgs = [r["organization"] for r in rs if norm_domain((r.get("organization") or {}).get("domain")) == domain]
        best = max((o for o in orgs if strong(o)), key=lambda o: o.get("employees_amount") or 0, default=None)
        if best or not res:
            return best or (orgs[0] if orgs else None)
        return max(res, key=lambda o: o.get("employees_amount") or 0)

    async def role_search(self, *, domain: str, role_title: str | None = None,
                          person_name: str | None = None, page: int = 1) -> list[dict]:
        body: dict[str, Any] = {"organization_domains": [domain], "role_is_current": True}
        if role_title:
            body["role_title"] = role_title
        if person_name:
            body["person_name"] = person_name
        if page > 1:
            body["page"] = page
        try:
            d = await self._post("person_role_search", "person_role_search/", body)
        except ApiError as e:
            if e.status != 402:
                raise
            log.warning("no Forager credits: serving role search from local cache", extra={"domain": domain})
            return self.cache.scan_roles(domain, person_name) if page == 1 else []
        return (d or {}).get("search_results", [])

    async def phone(self, slug: str) -> str | None:
        d = await self._post("phone_numbers", "person_contacts_lookup/phone_numbers/",
                             {"linkedin_public_identifier": slug})
        items = d if isinstance(d, list) else (d or {}).get("phone_numbers") or ([d] if d else [])
        for it in items:
            if n := e164(it.get("phone_number") if isinstance(it, dict) else it):
                return n
        return None

    async def personal_email(self, slug: str) -> str | None:
        d = await self._post("personal_emails", "person_contacts_lookup/personal_emails/",
                             {"linkedin_public_identifier": slug})
        if not d:
            return None
        return pick_email(d.get("emails")) or (d.get("email") or "").lower() or None

    async def work_email(self, slug: str, timeout: float = 90.0) -> str | None:
        """v2 async lookup: start job, poll status_check_url (polling is free)."""
        body = {"linkedin_public_identifier": slug}
        key = self.cache.key("work_emails", body)
        hit = self.cache.get(key)
        if hit is None:
            async with self.sem:
                self._guard()
                self._live("work_emails")
                r = await send(self.c, "POST", self.v2 + "person_contacts_lookup/work_emails/",
                               headers=self.h, json=body)
            self._guard(r)
            if r.status_code == 404:
                return None
            check(r)
            url = urljoin(self.s.forager_base, r.json()["status_check_url"])
            deadline = time.monotonic() + timeout
            while True:
                pr = await send(self.c, "GET", url, headers=self.h)
                check(pr)
                hit = pr.json()
                if hit.get("status") in ("completed", "failed"):
                    break
                if time.monotonic() > deadline:
                    log.warning("work email poll timed out", extra={"slug": slug})
                    return None
                await asyncio.sleep(max(1.0, retry_after(pr, 2.0)))
            if hit.get("status") != "completed":
                log.warning("work email lookup failed", extra={"slug": slug, "error": hit.get("error_code")})
                return None
            self.cache.set(key, hit)
        return pick_email(hit.get("emails"))
