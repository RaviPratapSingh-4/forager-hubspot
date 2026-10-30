from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .cache import Cache
from .config import get_settings, setup_logging
from .forager import Forager
from .http import ApiError
from .hubspot import HubSpot
from .poller import poll_loop
from .services import NotFound, Services, build_filter, summarize
from .webhooks import check_secret, router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    s = get_settings()
    s.need("forager_api_key", "hubspot_token", "webhook_secret")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        cache = Cache(s.cache_dir)
        app.state.settings = s
        hs = HubSpot(s, client)
        app.state.svc = Services(s, Forager(s, client, cache), hs, cache)
        every = float(os.getenv("POLL_SECONDS", "30"))  # 0 = webhooks only
        task = asyncio.create_task(poll_loop(hs, app.state.svc, every)) if every > 0 else None
        yield
        if task:
            task.cancel()


app = FastAPI(title="Forager x HubSpot enrichment", lifespan=lifespan)
app.include_router(webhook_router)


class CompanyReq(BaseModel):
    domain: str


class ContactReq(BaseModel):
    contact_id: str
    force: bool = False
    overwrite: bool = False


class FindReq(BaseModel):
    domain: str
    include_titles: list[str] | None = Field(None, description="any-of keywords; null = server default")
    exclude_titles: list[str] | None = Field(None, description="none-of keywords; null = server default")
    seniority: list[str] | None = Field(None, description="c_level, vp, director, head, manager")
    limit: int = Field(5, ge=1, le=25)
    enrich: bool = Field(False, description="look up email/phone (spends credits)")
    create_in_hubspot: bool = False
    company_id: str | None = None


@app.exception_handler(NotFound)
async def _nf(_: Request, e: NotFound):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(ApiError)
async def _api(_: Request, e: ApiError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": f"upstream error {e.status}", "url": e.url}, status_code=502)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/enrich/company", dependencies=[Depends(check_secret)])
async def enrich_company(req: CompanyReq, request: Request):
    return await request.app.state.svc.enrich_company(req.domain)


@app.post("/enrich/contact", dependencies=[Depends(check_secret)])
async def enrich_contact(req: ContactReq, request: Request):
    return await request.app.state.svc.enrich_contact(req.contact_id, req.force, req.overwrite)


@app.post("/find-contacts", dependencies=[Depends(check_secret)])
async def find_contacts(req: FindReq, request: Request):
    svc: Services = request.app.state.svc
    try:
        tf = build_filter(svc.s, req.include_titles, req.exclude_titles, req.seniority)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if req.create_in_hubspot:
        company_id = req.company_id or (await svc.enrich_company(req.domain))["company_id"]
        contacts = await svc.find_and_create(req.domain, company_id, tf, req.limit)
        return {"company_id": company_id, "contacts": contacts}
    items = await svc.select_contacts(req.domain.lower(), tf, req.limit, req.enrich)
    return {"contacts": [summarize(i["props"]) for i in items]}
