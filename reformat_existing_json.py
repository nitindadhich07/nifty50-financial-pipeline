"""
Migration script: reformat all existing company_financials.json files
to the new target schema:

{
  "company_info": { ticker, company_name, exchange, sector, industry, currency, unit },
  "profit_loss":  { "quarterly": {...}, "yearly": {...} },
  "balance_sheet": { "yearly": {...} },
  "cash_flow":    { "yearly": {...} },
  "ratios":       { "FY2025": {...}, ... },   ← flat, NOT nested under "annual"
  "metadata":     { data_sources, last_updated, parser_version, validation_passed }
}

Run from the project root:
    python reformat_existing_json.py
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ── Load universe for sector / industry lookup ──────────────────────────────
UNIVERSE_PATH = Path("pipeline_v3/config/nifty50_universe.json")
universe_data = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))

COMPANY_META: dict = {}  # symbol (upper) → {sector, industry, name}
for c in universe_data.get("companies", []):
    sym = str(c.get("symbol", "")).upper()
    COMPANY_META[sym] = {
        "company_name": c.get("name", sym),
        "sector": c.get("sector", ""),
        "industry": c.get("industry", ""),
    }

NOW_ISO = datetime.now(timezone.utc).isoformat()


def _drop_nulls(d: dict) -> dict:
    """Remove keys with None values from a dict (non-recursive)."""
    return {k: v for k, v in d.items() if v is not None}


def _clean_period(period_data: dict) -> dict:
    """Remove entries where ALL values are None, keep only non-empty periods."""
    result = {}
    for label, fields in period_data.items():
        if isinstance(fields, dict):
            clean = _drop_nulls(fields)
            if clean:
                result[label] = clean
    return result


def reformat(src: dict, symbol: str) -> dict:
    """
    Convert any existing schema variant to the target schema.
    Handles both old (flat annual/quarterly inside profit_loss) and new
    partially-updated schemas.
    """
    meta = COMPANY_META.get(symbol.upper(), {})

    # ── company_info ───────────────────────────────────────────────────────
    old_info: dict = src.get("company_info") or {}
    company_info = {
        "ticker": old_info.get("ticker", symbol.upper()),
        "company_name": old_info.get("company_name") or meta.get("company_name", symbol),
        "exchange": old_info.get("exchange", "NSE/BSE"),
        "sector": old_info.get("sector") or meta.get("sector", ""),
        "industry": old_info.get("industry") or meta.get("industry", ""),
        "currency": old_info.get("currency", "INR"),
        "unit": old_info.get("unit", "₹ Crores"),
    }

    # ── profit_loss ────────────────────────────────────────────────────────
    pl_src: dict = src.get("profit_loss") or {}

    # Support old schema where keys are year labels at the top level
    # and new schema where structure is { "quarterly": {...}, "yearly": {...} }
    if "quarterly" in pl_src or "yearly" in pl_src or "annual" in pl_src:
        pl_quarterly = _clean_period(pl_src.get("quarterly") or {})
        pl_yearly = _clean_period(pl_src.get("yearly") or pl_src.get("annual") or {})
    else:
        # Old flat schema – treat all as yearly
        pl_quarterly = {}
        pl_yearly = _clean_period(pl_src)

    # ── balance_sheet ──────────────────────────────────────────────────────
    bs_src: dict = src.get("balance_sheet") or {}
    if "yearly" in bs_src or "annual" in bs_src:
        bs_yearly = _clean_period(bs_src.get("yearly") or bs_src.get("annual") or {})
    elif "quarterly" in bs_src:
        bs_yearly = _clean_period(bs_src.get("quarterly") or {})
    else:
        bs_yearly = _clean_period(bs_src)

    # ── cash_flow ──────────────────────────────────────────────────────────
    cf_src: dict = src.get("cash_flow") or {}
    if "yearly" in cf_src or "annual" in cf_src:
        cf_yearly = _clean_period(cf_src.get("yearly") or cf_src.get("annual") or {})
    elif "quarterly" in cf_src:
        cf_yearly = _clean_period(cf_src.get("quarterly") or {})
    else:
        cf_yearly = _clean_period(cf_src)

    # ── ratios ─────────────────────────────────────────────────────────────
    ratios_src = src.get("ratios") or {}
    # Support nested {"annual": {"FY2025": {...}}} or flat {"FY2025": {...}}
    if "annual" in ratios_src and isinstance(ratios_src["annual"], dict):
        flat_ratios = _clean_period(ratios_src["annual"])
    elif "quarterly" in ratios_src or "yearly" in ratios_src:
        flat_ratios = _clean_period(ratios_src.get("yearly") or ratios_src.get("annual") or {})
    else:
        # Already flat or empty
        flat_ratios = _clean_period(ratios_src) if ratios_src else {}
        # If keys look like FY years, keep as-is
        if flat_ratios and not any(str(k).startswith("FY") for k in flat_ratios):
            flat_ratios = {}

    # ── metadata ───────────────────────────────────────────────────────────
    old_meta: dict = src.get("metadata") or {}
    # Preserve existing data_sources if available, otherwise empty list
    existing_sources = old_meta.get("data_sources", [])
    if not isinstance(existing_sources, list):
        existing_sources = []

    # Build a deduplicated data_sources from provenance if we have no explicit sources
    if not existing_sources:
        prov = old_meta.get("provenance") or {}
        seen: set = set()
        for period_type, years in prov.items():
            if not isinstance(years, dict):
                continue
            for year, stmts in years.items():
                if not isinstance(stmts, dict):
                    continue
                for stmt, fields in stmts.items():
                    if not isinstance(fields, dict):
                        continue
                    for fld, fmeta in fields.items():
                        if not isinstance(fmeta, dict):
                            continue
                        src_name = fmeta.get("source", "UNKNOWN")
                        key = f"{src_name}|{year}"
                        if key not in seen:
                            seen.add(key)
                            existing_sources.append({"type": src_name, "label": year})
                        break  # one per stmt per year
                    break

    clean_metadata = {
        "data_sources": existing_sources,
        "last_updated": old_meta.get("last_updated", NOW_ISO),
        "parser_version": old_meta.get("parser_version", "v2.0"),
        "validation_passed": old_meta.get("validation_passed", True),
    }

    return {
        "company_info": company_info,
        "profit_loss": {
            "quarterly": pl_quarterly,
            "yearly": pl_yearly,
        },
        "balance_sheet": {
            "yearly": bs_yearly,
        },
        "cash_flow": {
            "yearly": cf_yearly,
        },
        "ratios": flat_ratios,
        "metadata": clean_metadata,
    }


def process_file(path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️  Skipping (read error): {path} → {e}")
        return

    # Determine ticker from company_info or folder name
    ticker = (raw.get("company_info") or {}).get("ticker", "")
    if not ticker:
        # Guess from path like data/standard/tcs/final/...
        parts = path.parts
        for i, part in enumerate(parts):
            if part == "final" and i > 0:
                ticker = parts[i - 1].upper()
                break

    try:
        reformatted = reformat(raw, ticker)
    except Exception as e:
        print(f"  ⚠️  Skipping (reformat error): {path} → {e}")
        return

    path.write_text(
        json.dumps(reformatted, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  ✅  Reformatted: {path}")


def main():
    data_root = Path("data")
    json_files = list(data_root.rglob("*/final/company_financials.json"))
    print(f"Found {len(json_files)} company_financials.json file(s) to reformat.\n")

    for f in sorted(json_files):
        process_file(f)

    print(f"\n✨ Done. {len(json_files)} file(s) processed.")


if __name__ == "__main__":
    main()
