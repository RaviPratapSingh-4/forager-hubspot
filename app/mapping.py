from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

FREEMAIL = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "proton.me", "protonmail.com", "aol.com", "live.com", "me.com",
}
JUNK = {"n/a", "na", "null", "none", "unknown", "-", "--"}
MULTILINE = {"description", "forager_person_description"}
ACRONYMS = re.compile(
    r"\b(ceo|cto|cfo|coo|cmo|cio|ciso|cpo|cro|vp|svp|evp|avp|hr|it|ai|ml|us|uk|qa|ux|ui|seo|gtm|b2b|b2c)\b", re.I
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_domain(v: str | None) -> str:
    v = (v or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0]
    return v.removeprefix("www.")


def clean_str(v: Any, multiline: bool = False) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    if multiline:
        s = "\n".join(line.strip() for line in str(v).strip().splitlines())
        s = re.sub(r"\n{3,}", "\n\n", s)
    else:
        s = " ".join(str(v).split())
    return None if not s or s.lower() in JUNK else s


def clean(props: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in props.items():
        c = clean_str(v, multiline=k in MULTILINE)
        if c is not None:
            out[k] = c
    return out


def norm_case(v: Any) -> str | None:
    s = clean_str(v)
    return s.title() if s and (s.isupper() or s.islower()) else s


def norm_title(v: Any) -> str | None:
    s = clean_str(v)
    if s and (s.isupper() or s.islower()):
        s = ACRONYMS.sub(lambda m: m.group(0).upper(), s.title())
    return s


def parse_location(loc: dict | None) -> dict[str, str]:
    """Forager location: {name, osm_locations:[{name, place_type}]} -> city/state/country."""
    out: dict[str, str] = {}
    if not loc:
        return out
    for o in loc.get("osm_locations") or []:
        t, n = (o.get("place_type") or "").lower(), clean_str((o.get("name") or "").split(",")[0])
        if not n:
            continue
        if t in {"city", "town", "village", "municipality"}:
            out.setdefault("city", n)
        elif t in {"state", "province", "region"}:
            out.setdefault("state", n)
        elif t == "country":
            out.setdefault("country", n)
    parts = [p.strip() for p in (loc.get("name") or "").split(",") if p.strip()]
    if loc.get("osm_locations"):
        parts = []  # trust structured data; free-text name is only a fallback
    if len(parts) >= 3:
        out.setdefault("city", parts[0])
        out.setdefault("state", parts[-2])
        out.setdefault("country", parts[-1])
    elif len(parts) == 2:
        out.setdefault("city", parts[0])
        out.setdefault("country", parts[1])
    elif len(parts) == 1:
        out.setdefault("country", parts[0])
    return {k: norm_case(v) for k, v in out.items() if norm_case(v)}


COUNTRY_NAMES = {
    "US": "United States", "USA": "United States", "GB": "United Kingdom", "UK": "United Kingdom",
    "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France", "IN": "India", "IE": "Ireland",
    "NL": "Netherlands", "SG": "Singapore", "JP": "Japan", "CH": "Switzerland", "ES": "Spain",
    "IT": "Italy", "SE": "Sweden", "IL": "Israel", "BR": "Brazil", "CN": "China", "AE": "United Arab Emirates",
}
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District Of Columbia",
}


def country_name(v: Any) -> str | None:
    s = clean_str(v)
    return COUNTRY_NAMES.get(s.upper(), s) if s and len(s) <= 3 else norm_case(s)


def state_name(v: Any, country: str | None = None) -> str | None:
    s = clean_str(v)
    if s and len(s) == 2 and (country or "United States") == "United States":
        return US_STATES.get(s.upper(), s)
    return norm_case(s)


def e164(v: Any) -> str | None:
    s = re.sub(r"[^\d+]", "", str(v or ""))
    digits = s.lstrip("+")
    return f"+{digits}" if s.startswith("+") and 8 <= len(digits) <= 15 else None


# HubSpot's fixed industry enum (subset of LinkedIn-style names); unknown values are dropped, not sent.
HS_INDUSTRIES = set("""ACCOUNTING AIRLINES_AVIATION ALTERNATIVE_DISPUTE_RESOLUTION ALTERNATIVE_MEDICINE ANIMATION APPAREL_FASHION
ARCHITECTURE_PLANNING ARTS_AND_CRAFTS AUTOMOTIVE AVIATION_AEROSPACE BANKING BIOTECHNOLOGY BROADCAST_MEDIA
BUILDING_MATERIALS BUSINESS_SUPPLIES_AND_EQUIPMENT CAPITAL_MARKETS CHEMICALS CIVIC_SOCIAL_ORGANIZATION CIVIL_ENGINEERING
COMMERCIAL_REAL_ESTATE COMPUTER_NETWORK_SECURITY COMPUTER_GAMES COMPUTER_HARDWARE COMPUTER_NETWORKING COMPUTER_SOFTWARE
INTERNET CONSTRUCTION CONSUMER_ELECTRONICS CONSUMER_GOODS CONSUMER_SERVICES COSMETICS DAIRY DEFENSE_SPACE DESIGN
EDUCATION_MANAGEMENT E_LEARNING ELECTRICAL_ELECTRONIC_MANUFACTURING ENTERTAINMENT ENVIRONMENTAL_SERVICES EVENTS_SERVICES
EXECUTIVE_OFFICE FACILITIES_SERVICES FARMING FINANCIAL_SERVICES FINE_ART FISHERY FOOD_BEVERAGES FOOD_PRODUCTION FUND_RAISING
FURNITURE GAMBLING_CASINOS GLASS_CERAMICS_CONCRETE GOVERNMENT_ADMINISTRATION GOVERNMENT_RELATIONS GRAPHIC_DESIGN
HEALTH_WELLNESS_AND_FITNESS HIGHER_EDUCATION HOSPITAL_HEALTH_CARE HOSPITALITY HUMAN_RESOURCES IMPORT_AND_EXPORT
INDIVIDUAL_FAMILY_SERVICES INDUSTRIAL_AUTOMATION INFORMATION_SERVICES INFORMATION_TECHNOLOGY_AND_SERVICES INSURANCE
INTERNATIONAL_AFFAIRS INTERNATIONAL_TRADE_AND_DEVELOPMENT INVESTMENT_BANKING INVESTMENT_MANAGEMENT JUDICIARY
LAW_ENFORCEMENT LAW_PRACTICE LEGAL_SERVICES LEGISLATIVE_OFFICE LEISURE_TRAVEL_TOURISM LIBRARIES LOGISTICS_AND_SUPPLY_CHAIN
LUXURY_GOODS_JEWELRY MACHINERY MANAGEMENT_CONSULTING MARITIME MARKET_RESEARCH MARKETING_AND_ADVERTISING
MECHANICAL_OR_INDUSTRIAL_ENGINEERING MEDIA_PRODUCTION MEDICAL_DEVICES MEDICAL_PRACTICE MENTAL_HEALTH_CARE MILITARY
MINING_METALS MOTION_PICTURES_AND_FILM MUSEUMS_AND_INSTITUTIONS MUSIC NANOTECHNOLOGY NEWSPAPERS NON_PROFIT_ORGANIZATION_MANAGEMENT
OIL_ENERGY ONLINE_MEDIA OUTSOURCING_OFFSHORING PACKAGE_FREIGHT_DELIVERY PACKAGING_AND_CONTAINERS PAPER_FOREST_PRODUCTS
PERFORMING_ARTS PHARMACEUTICALS PHILANTHROPY PHOTOGRAPHY PLASTICS POLITICAL_ORGANIZATION PRIMARY_SECONDARY_EDUCATION PRINTING
PROFESSIONAL_TRAINING_COACHING PROGRAM_DEVELOPMENT PUBLIC_POLICY PUBLIC_RELATIONS_AND_COMMUNICATIONS PUBLIC_SAFETY PUBLISHING
RAILROAD_MANUFACTURE RANCHING REAL_ESTATE RECREATIONAL_FACILITIES_AND_SERVICES RELIGIOUS_INSTITUTIONS RENEWABLES_ENVIRONMENT
RESEARCH RESTAURANTS RETAIL SECURITY_AND_INVESTIGATIONS SEMICONDUCTORS SHIPBUILDING SPORTING_GOODS SPORTS STAFFING_AND_RECRUITING
SUPERMARKETS TELECOMMUNICATIONS TEXTILES THINK_TANKS TOBACCO TRANSLATION_AND_LOCALIZATION TRANSPORTATION_TRUCKING_RAILROAD
UTILITIES VENTURE_CAPITAL_PRIVATE_EQUITY VETERINARY WAREHOUSING WHOLESALE WINE_AND_SPIRITS WIRELESS WRITING_AND_EDITING""".split())
INDUSTRY_ALIASES = {
    "TECHNOLOGY_INFORMATION_AND_INTERNET": "INTERNET",
    "SOFTWARE_DEVELOPMENT": "COMPUTER_SOFTWARE",
    "IT_SERVICES_AND_IT_CONSULTING": "INFORMATION_TECHNOLOGY_AND_SERVICES",
}


def hubspot_industry(name: str | None) -> str | None:
    if not name:
        return None
    k = re.sub(r"[^A-Z0-9]+", "_", name.upper().replace("&", "AND")).strip("_")
    k = INDUSTRY_ALIASES.get(k, k)
    return k if k in HS_INDUSTRIES else None


def _year(d: str | None) -> str | None:
    return d[:4] if d and d[:4].isdigit() else None


def company_props(org: dict) -> dict[str, str]:
    addrs = [a for a in (org.get("addresses") or []) if a] or [{}]
    addr = max(addrs, key=lambda a: sum(1 for v in a.values() if v))
    loc = parse_location(org.get("location"))
    li = org.get("linkedin_info") or {}
    ind = (li.get("industry") or {}).get("name")
    street = " ".join(x for x in [addr.get("street_number"), addr.get("street_name")] if x)
    domain = norm_domain(org.get("domain") or org.get("website"))
    return clean({
        "name": org.get("name"),
        "domain": domain,
        "website": org.get("website") or (f"https://{domain}" if domain else None),
        "description": org.get("description"),
        "industry": hubspot_industry(ind),
        "numberofemployees": org.get("employees_amount"),
        "annualrevenue": (org.get("finance_info") or {}).get("revenue"),
        "address": street,
        "city": loc.get("city") or norm_case(addr.get("city")),
        "state": loc.get("state") or state_name(addr.get("state"), country_name(addr.get("country"))),
        "zip": addr.get("postcode"),
        "country": loc.get("country") or country_name(addr.get("country")),
        "founded_year": _year(org.get("founded_date")),
        "linkedin_company_page": li.get("public_profile_url"),
        "forager_industry": ind,
        "forager_employees_range": org.get("employees_range"),
        "forager_linkedin_url": li.get("public_profile_url"),
        "forager_operating_status": org.get("operating_status"),
        "forager_logo_url": org.get("logo"),
        "forager_keywords": ", ".join(k["name"] for k in (org.get("keywords") or [])[:15] if k.get("name")),
        "forager_company_id": org.get("id"),
    })


def person_props(res: dict, email: str | None, phone: str | None) -> dict[str, str]:
    """res = one person_role_search result."""
    p = res.get("person") or {}
    org = res.get("organization") or {}
    li = p.get("linkedin_info") or {}
    oli = org.get("linkedin_info") or {}
    loc = parse_location(p.get("location"))
    first, last = p.get("first_name"), p.get("last_name")
    if not first and p.get("full_name"):
        first, _, rest = p["full_name"].partition(" ")
        last = last or rest
    return clean({
        "firstname": norm_case(first),
        "lastname": norm_case(last),
        "jobtitle": norm_title(res.get("role_title") or p.get("headline")),
        "email": (email or "").lower() or None,
        "phone": phone,
        "city": loc.get("city"),
        "state": loc.get("state"),
        "country": loc.get("country"),
        "company": org.get("name"),
        "website": norm_domain(org.get("domain")),
        "forager_company_linkedin_url": oli.get("public_profile_url"),
        "forager_linkedin_url": li.get("public_profile_url"),
        "forager_person_id": p.get("id"),
        "forager_headline": p.get("headline"),
        "forager_person_description": p.get("description"),
        "forager_skills": ", ".join(s["name"] for s in (p.get("skills") or [])[:15] if s.get("name")),
    })


def linkedin_slug(res: dict) -> str | None:
    return (((res.get("person") or {}).get("linkedin_info")) or {}).get("public_identifier")
