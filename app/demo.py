"""Idempotent demo: python -m app.demo [--domain openai.com] [--count 5] [--forager-only] [--yes]"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from .cache import Cache
from .config import get_settings, setup_logging
from .forager import Forager
from .hubspot import HubSpot
from .services import Services, build_filter, summarize
from .setup_properties import run as setup_props


async def main(a: argparse.Namespace) -> None:
    setup_logging()
    s = get_settings()
    print(f"Plan: 1 organization_search + 1-2 person_role_search + up to {a.count} x (work_email + phone) "
          f"for {a.domain}. Cached calls are free; lookups bill only when a result is returned. "
          "Note your Forager credit balance now.")
    if not a.yes and input("Proceed with live Forager calls? [y/N] ").lower() != "y":
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        cache = Cache(s.cache_dir)
        forager = Forager(s, client, cache)
        tf = build_filter(s, a.include, a.exclude, a.seniority)
        if a.forager_only:
            svc = Services(s, forager, None, cache)
            org = await forager.organization(a.domain, tf.server_query())
            print("COMPANY:", json.dumps(org and __import__("app.mapping", fromlist=["x"]).company_props(org), indent=1))
            for it in await svc.select_contacts(a.domain, tf, a.count, True, tuple(a.require), not a.allow_partial):
                print("PERSON:", json.dumps(summarize(it["props"]), indent=1))
        else:
            s.need("hubspot_token")
            await setup_props()
            svc = Services(s, forager, HubSpot(s, client), cache)
            co = await svc.enrich_company(a.domain)
            print(f"Company {a.domain}: hubspot id {co['company_id']} (created={co['created']})")
            for c in await svc.find_and_create(a.domain, co["company_id"], tf, a.count, tuple(a.require), not a.allow_partial):
                print(json.dumps(c))
        print(forager.report())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--domain", default="openai.com")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--include", nargs="*", default=None)
    p.add_argument("--exclude", nargs="*", default=None)
    p.add_argument("--seniority", nargs="*", default=None)
    p.add_argument("--require", nargs="*", default=["email", "phone", "city", "state", "country"])
    p.add_argument("--allow-partial", action="store_true", help="also write people missing required fields")
    p.add_argument("--forager-only", action="store_true", help="no HubSpot calls; caches Forager responses")
    p.add_argument("--yes", action="store_true")
    asyncio.run(main(p.parse_args()))
