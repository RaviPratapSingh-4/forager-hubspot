from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from .cache import Cache
from .config import Settings
from .forager import Forager
from .http import ApiError
from .hubspot import COMPANY_READ, CONTACT_READ, HubSpot
from .mapping import FREEMAIL, company_props, linkedin_slug, norm_domain, now_iso, parse_location, person_props
from .titles import TitleFilter

log = logging.getLogger("app")


class NotFound(Exception):
    pass


def build_filter(s: Settings, include=None, exclude=None, seniority=None) -> TitleFilter:
    """None -> configured default; [] -> explicitly no constraint."""
    pick = lambda v, d: tuple(d if v is None else v)  # noqa: E731
    return TitleFilter(pick(include, s.default_include), pick(exclude, s.default_exclude),
                       pick(seniority, s.default_seniority))


def summarize(props: dict, hubspot_id: str | None = None, created: bool | None = None) -> dict:
    keys = ("firstname", "lastname", "jobtitle", "email", "phone", "city", "state", "country",
            "company", "forager_linkedin_url")
    d = {k: props.get(k) for k in keys}
    d["hubspot_id"], d["created"] = hubspot_id, created
    return d


class Services:
    def __init__(self, s: Settings, forager: Forager, hs: HubSpot | None, cache: Cache) -> None:
        self.s, self.forager, self.hs, self.cache = s, forager, hs, cache
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.seen: set[tuple[str, str]] = set()

    # ---------- Forager-only ----------
    async def find_candidates(self, domain: str, tf: TitleFilter, max_candidates: int, max_pages: int = 2) -> list[dict]:
        found: list[tuple[int, dict]] = []
        for page in range(1, max_pages + 1):
            results = await self.forager.role_search(domain=domain, role_title=tf.server_query(), page=page)
            if not results:
                break
            for r in results:
                title = r.get("role_title") or (r.get("person") or {}).get("headline") or ""
                odom = norm_domain((r.get("organization") or {}).get("domain"))
                last = ((r.get("person") or {}).get("last_name") or "").replace(".", "").strip()
                if r.get("is_current") is False or (odom and odom != domain) or not linkedin_slug(r) or len(last) < 2:
                    continue  # also skips abbreviated last names ("Scott S.")
                if tf.matches(title):
                    found.append((tf.rank(title), r))
            if len({linkedin_slug(r) for _, r in found}) >= max_candidates:
                break
        found.sort(key=lambda x: x[0])
        seen, out = set(), []
        for _, r in found:
            slug = linkedin_slug(r)
            if slug not in seen:
                seen.add(slug)
                out.append(r)
        return out[:max_candidates]

    async def _safe(self, coro):
        try:
            return await coro
        except Exception as e:  # fail-soft per lookup: never let one task kill the batch
            log.warning("forager lookup failed", extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
            return None

    async def build_contact(self, cand: dict) -> dict:
        slug = linkedin_slug(cand)
        email, phone = await asyncio.gather(self._safe(self.forager.work_email(slug)),
                                            self._safe(self.forager.phone(slug)))
        if not email and self.s.personal_email_fallback:
            email = await self._safe(self.forager.personal_email(slug))
        return person_props(cand, email, phone)

    async def select_contacts(self, domain: str, tf: TitleFilter, limit: int, enrich: bool,
                              require: tuple[str, ...] = (), strict: bool = False) -> list[dict]:
        """Rank candidates, look up contact data in batches sized to what is still missing."""
        cands = await self.find_candidates(domain, tf, self.s.max_candidates)
        loc_req = [k for k in require if k in ("city", "state", "country")]
        label = domain.split(".")[0]

        def quality(c: dict) -> tuple[int, int]:  # free signals, used BEFORE spending credits
            p = c["person"]
            loc = parse_location(p.get("location"))
            return (0 if label in (p.get("headline") or "").lower() else 1,  # headline names the company
                    sum(1 for k in loc_req if not loc.get(k)))  # missing city/state/country
        cands = sorted(cands, key=quality)  # stable: keeps seniority order within equal quality
        if not enrich:
            return [{"cand": c, "props": person_props(c, None, None)} for c in cands[:limit]]
        chosen: list[dict] = []
        partial: list[dict] = []
        i = 0
        while len(chosen) < limit and i < len(cands):
            batch = cands[i:i + (limit - len(chosen))]
            i += len(batch)
            built = await asyncio.gather(*(self.build_contact(c) for c in batch), return_exceptions=True)
            built = [{} if isinstance(b, Exception) else b for b in built]
            for c, props in zip(batch, built):
                (chosen if all(props.get(k) for k in require) else partial).append({"cand": c, "props": props})
        if not strict:
            chosen += partial[: max(0, limit - len(chosen))]
        if len(chosen) < limit:
            log.warning("fewer contacts than requested", extra={"wanted": limit, "got": len(chosen)})
        return chosen[:limit]

    # ---------- HubSpot writes ----------
    async def _upsert(self, kind: str, memo_key: str, existing: str | None, props: dict) -> tuple[str, bool]:
        if existing:
            try:
                await self.hs.update(kind, existing, props)
                return existing, False
            except ApiError as e:
                if e.status != 404:
                    raise
        r = await self.hs.create(kind, props)
        oid = str(r["id"])
        if r.get("existing"):
            await self.hs.update(kind, oid, props)
        self.cache.memo_set(memo_key, oid)
        return oid, not r.get("existing")

    async def enrich_company(self, domain: str, company_id: str | None = None, mark: bool = True) -> dict:
        domain = norm_domain(domain)
        org = await self.forager.organization(domain, build_filter(self.s).server_query())
        if not org:
            raise NotFound(f"no Forager organization for {domain}")
        props = company_props(org)
        props["domain"] = domain
        if mark:
            props["forager_enriched_at"] = now_iso()
        key = f"company:{domain}"
        async with self.locks[key]:
            existing = company_id or self.cache.memo_get(key)
            if not existing:
                f = await self.hs.find("companies", "domain", domain)
                existing = f["id"] if f else None
            cid, created = await self._upsert("companies", key, existing, props)
        log.info("company enriched", extra={"company_id": cid, "domain": domain, "was_created": created})
        return {"company_id": cid, "created": created, "properties": props}

    async def upsert_contact(self, props: dict, company_id: str | None) -> tuple[str, bool]:
        email = (props.get("email") or "").lower() or None
        li = props.get("forager_linkedin_url")
        key = f"contact:{email}" if email else f"contact:li:{li}"
        async with self.locks[key]:
            existing = self.cache.memo_get(key)
            if not existing and email:
                f = await self.hs.find("contacts", "email", email, ("email",))
                existing = f["id"] if f else None
            if not existing and li:
                f = await self.hs.find("contacts", "forager_linkedin_url", li, ("email",))
                existing = f["id"] if f else None
            cid, created = await self._upsert("contacts", key, existing, props)
            if company_id:
                await self.hs.associate(cid, company_id)
        return cid, created

    async def find_and_create(self, domain: str, company_id: str | None, tf: TitleFilter, limit: int,
                              require: tuple[str, ...] = (), strict: bool = False) -> list[dict]:
        out = []
        for it in await self.select_contacts(domain, tf, limit, True, require, strict):
            props = dict(it["props"], forager_enriched_at=now_iso())  # marker set at creation -> no webhook loop
            cid, created = await self.upsert_contact(props, company_id)
            out.append(summarize(props, cid, created))
        return out

    # ---------- enrich an existing HubSpot contact ----------
    async def _contact_domain(self, pr: dict, contact_id: str) -> str:
        if d := norm_domain(pr.get("website")):
            return d
        em = pr.get("email") or ""
        if "@" in em and (d := em.split("@", 1)[1].lower()) not in FREEMAIL:
            return d
        for coid in await self.hs.contact_company_ids(contact_id):
            co = await self.hs.get("companies", coid, ["domain"])
            if d := norm_domain(co["properties"].get("domain")):
                return d
        return ""

    async def enrich_contact(self, contact_id: str, force: bool = False, overwrite: bool = False) -> dict:
        pr = (await self.hs.get("contacts", contact_id, CONTACT_READ))["properties"]
        if pr.get("forager_enriched_at") and not force:
            return {"contact_id": contact_id, "skipped": "already_enriched"}
        first, last = (pr.get("firstname") or "").strip(), (pr.get("lastname") or "").strip()
        domain = await self._contact_domain(pr, contact_id)
        if not (first and last and domain):
            log.info("contact lacks name/domain; cannot match in Forager", extra={"contact_id": contact_id})
            return {"contact_id": contact_id, "skipped": "insufficient_data (need first+last name and a company domain)"}
        results = await self.forager.role_search(domain=domain, person_name=f'"{first} {last}"')
        cand = next((r for r in results if linkedin_slug(r)
                     and (r["person"].get("first_name") or "").lower() == first.lower()
                     and (r["person"].get("last_name") or "").lower() == last.lower()), None)
        if not cand:
            return {"contact_id": contact_id, "skipped": "no_forager_match"}
        new = await self.build_contact(cand)
        upd = {k: v for k, v in new.items() if k.startswith("forager_") or overwrite or not pr.get(k)}
        upd["forager_enriched_at"] = now_iso()
        await self.hs.update("contacts", contact_id, upd)
        if not await self.hs.contact_company_ids(contact_id):
            co = await self.hs.find("companies", "domain", domain)
            if co:
                await self.hs.associate(contact_id, co["id"])
        log.info("contact enriched", extra={"contact_id": contact_id, "fields": sorted(upd)})
        return {"contact_id": contact_id, "updated": summarize({**pr, **upd})}

    # ---------- webhook handlers ----------
    async def on_company_created(self, company_id: str) -> None:
        pr = (await self.hs.get("companies", company_id, COMPANY_READ))["properties"]
        if pr.get("forager_enriched_at"):
            log.info("company already enriched; skip", extra={"company_id": company_id})
            return
        domain = norm_domain(pr.get("domain") or pr.get("website"))
        if not domain:
            log.info("company has no domain; skip", extra={"company_id": company_id})
            return
        await self.enrich_company(domain, company_id, mark=False)
        ids = await self.hs.company_contact_ids(company_id)
        if ids:
            for cid in ids:
                try:
                    await self.enrich_contact(cid)
                except Exception:
                    log.exception("contact enrich failed", extra={"contact_id": cid})
        else:
            await self.find_and_create(domain, company_id, build_filter(self.s), self.s.default_limit)
        await self.hs.update("companies", company_id, {"forager_enriched_at": now_iso()})

    async def handle_event(self, typ: str, oid: str) -> None:
        key = (typ, oid)
        if key in self.seen:
            log.info("duplicate delivery ignored", extra={"type": typ, "id": oid})
            return
        if len(self.seen) > 10000:
            self.seen.clear()
        self.seen.add(key)
        try:
            if typ == "company":
                await self.on_company_created(oid)
            elif typ == "contact":
                await self.enrich_contact(oid)
            else:
                log.warning("unknown event type", extra={"type": typ})
        except Exception:
            self.seen.discard(key)
            log.exception("event failed", extra={"type": typ, "id": oid})
