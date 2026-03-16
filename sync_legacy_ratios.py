"""
Sync ratios from canonical new-pipeline files to old flat-path legacy files.

Canonical locations:
  data/standard/<ticker>/final/company_financials.json
  data/bank/<ticker>/final/company_financials.json
  data/nbfc/<ticker>/final/company_financials.json
  data/insurance/<ticker>/final/company_financials.json
  data/utility/<ticker>/final/company_financials.json

Legacy (old) locations:
  data/<ticker>/final/company_financials.json   ← get ratios synced here

Run from project root:
    python3 sync_legacy_ratios.py
"""

import json
from pathlib import Path

CANONICAL_DIRS = ["standard", "bank", "nbfc", "insurance", "utility"]
DATA_ROOT = Path("data")


def build_canonical_map() -> dict:
    """Returns {ticker_upper: Path_to_canonical_json}"""
    canon = {}
    for sector_dir in CANONICAL_DIRS:
        for jf in (DATA_ROOT / sector_dir).rglob("*/final/company_financials.json"):
            ticker = jf.parent.parent.name.upper()
            canon[ticker] = jf
    return canon


def main():
    canon_map = build_canonical_map()
    print(f"Found {len(canon_map)} canonical files.\n")

    # Find legacy flat files: data/<ticker>/final/company_financials.json
    # These are directly under DATA_ROOT (not in sub-sector folders)
    legacy_files = []
    for jf in DATA_ROOT.glob("*/final/company_financials.json"):
        sector_slug = jf.parent.parent.name
        if sector_slug not in CANONICAL_DIRS:
            legacy_files.append(jf)

    print(f"Found {len(legacy_files)} legacy flat files to fix.\n")

    fixed = 0
    skipped = 0
    for legacy_path in sorted(legacy_files):
        # Read legacy file
        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        ticker = (legacy_data.get("company_info") or {}).get("ticker", "").upper()
        if not ticker:
            ticker = legacy_path.parent.parent.name.upper()

        # Find canonical equivalent
        canon_path = canon_map.get(ticker)
        if not canon_path:
            print(f"  ⚠️  No canonical file for {ticker} → skip")
            skipped += 1
            continue

        canon_data = json.loads(canon_path.read_text(encoding="utf-8"))
        canon_ratios = canon_data.get("ratios") or {}

        if not canon_ratios:
            print(f"  ⚠️  Canonical file also has no ratios for {ticker} → skip")
            skipped += 1
            continue

        # Overwrite ratios in legacy file with canonical
        legacy_data["ratios"] = canon_ratios

        # Also sync company_info fields that canonical has but legacy may miss
        canonical_info = canon_data.get("company_info") or {}
        legacy_info = legacy_data.get("company_info") or {}
        for field in ("exchange", "sector", "industry", "currency"):
            if canonical_info.get(field) and not legacy_info.get(field):
                legacy_info[field] = canonical_info[field]
        legacy_data["company_info"] = legacy_info

        legacy_path.write_text(
            json.dumps(legacy_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        n_fy = len([v for v in canon_ratios.values() if v])
        print(f"  ✅  {ticker:<14} → synced {n_fy} FY ratio set(s) from {canon_path}")
        fixed += 1

    print(f"\n📊 Summary: {fixed} legacy files fixed, {skipped} skipped.")


if __name__ == "__main__":
    main()
