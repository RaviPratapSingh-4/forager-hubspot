# Forager x HubSpot enrichment (FastAPI)
1. `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. `cp .env.example .env` and fill FORAGER_API_KEY, HUBSPOT_TOKEN, WEBHOOK_SECRET (keys live only in env vars).
3. `python -m app.setup_properties` creates custom HubSpot properties (idempotent).
4. Demo (OpenAI + 5 people, idempotent): `python -m app.demo` (`--forager-only` first to cache Forager data without touching HubSpot).
5. Run: `uvicorn app.main:app`; all non-health routes need header `X-Webhook-Secret`.
6. Webhook: `POST /webhooks/hubspot?type=company|contact&secret=...` (creation events only; HubSpot v3 signature also accepted).
7. Title filter: `POST /find-contacts {"domain":"openai.com","include_titles":["engineer"],"exclude_titles":["intern"],"seniority":["director","vp"],"limit":5}` (add `"enrich":true` for email/phone, `"create_in_hubspot":true` to write).
8. Seniority levels: c_level, vp, director, head, manager. Match = any include AND any seniority AND no exclude (word-boundary, case-insensitive).
9. Tests: `pytest -q`. Forager responses are cached in `.cache/` so nothing is paid for twice.

## Interesting extras from Forager worth discussing
(Finalize after the first live run; fields below are in the API schema.) Person: headline, description (LinkedIn "About"), skills, gender, photo, role start date/duration, full role history. Organization: keywords, domain rank/traffic, web technologies, funding events (type, total, date), job-post events (title, remote, active), simple events (Product Hunt, Form D/C), employee range, operating status, logo. Stored as custom properties: forager_headline, forager_person_description, forager_skills, forager_keywords, forager_industry, forager_employees_range.
