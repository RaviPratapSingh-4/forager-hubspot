from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


class Cache:
    """Disk cache for Forager responses (so nothing is paid for twice) + HubSpot id memo."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        (self.root / "forager").mkdir(parents=True, exist_ok=True)
        self.memo_path = self.root / "ids.json"

    @staticmethod
    def key(name: str, body: Any) -> str:
        h = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        return f"{name}__{h}"

    def get(self, key: str) -> Any | None:
        p = self.root / "forager" / f"{key}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def set(self, key: str, val: Any) -> None:
        p = self.root / "forager" / f"{key}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(val, indent=1))
        os.replace(tmp, p)

    def scan_roles(self, domain: str, person_name: str | None = None) -> list[dict]:
        """Offline fallback: every cached role-search result for this domain (optionally one person)."""
        want = (person_name or "").replace('"', "").strip().lower()
        out, seen = [], set()
        for p in sorted((self.root / "forager").glob("person_role_search__*.json")):
            try:
                d = json.loads(p.read_text())
            except ValueError:
                continue
            for r in (d.get("search_results", []) if isinstance(d, dict) else []):
                dom = ((r.get("organization") or {}).get("domain") or "").lower().removeprefix("www.")
                person = r.get("person") or {}
                if dom != domain or person.get("id") in seen:
                    continue
                if want and (person.get("full_name") or "").lower() != want:
                    continue
                seen.add(person.get("id"))
                out.append(r)
        return out

    def _memo(self) -> dict:
        return json.loads(self.memo_path.read_text()) if self.memo_path.exists() else {}

    def memo_get(self, k: str) -> str | None:
        return self._memo().get(k)

    def memo_set(self, k: str, v: str) -> None:
        m = self._memo()
        m[k] = v
        self.memo_path.write_text(json.dumps(m, indent=1))
