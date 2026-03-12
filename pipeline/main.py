#!/usr/bin/env python3
"""
main.py  (FIXED v3) — Financial Pipeline Orchestrator
=======================================================
FIX 6: Removed the aggressive FY2025-only hard filter that was silently
        discarding all extracted data whenever FY label assignment differed
        slightly (e.g., "FY2025" vs "FY 2025" or a slightly off header parse).
        The filter is now configurable via --target-fy and defaults to keeping
        the 3 most recent years.

FIX 7: Removed the hard-coded RIL company-website URL which was 404'ing.
        Annual report downloads now rely on NSE/BSE announcements only, which
        provide the actual filed PDF URLs.

Usage:
  python3 -m pipeline.main
  python3 -m pipeline.main --symbol RELIANCE
  python3 -m pipeline.main --skip-download
  python3 -m pipeline.main --target-fy FY2025   # strict single-year filter
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

os.makedirs("logs", exist_ok=True)
_log_file = os.path.join("logs", f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

from pipeline.xbrl_parser        import NSEXBRLClient
from pipeline.downloader          import download_all_pdfs, COMPANY_REGISTRY
from pipeline.pdf_parser          import PDFParser
from pipeline.financial_extractor import FinancialExtractor
from pipeline.validator           import validate_all
from pipeline.export_json         import build_output, save_json, print_summary

BASE_DATA_DIR  = "data"
DEFAULT_SYMBOL = "RELIANCE"


# ── Step 1: XBRL ──────────────────────────────────────────────────────────────

def step_xbrl(symbol: str, target_fy: Optional[str] = None) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 1: NSE XBRL — {symbol}\n{'#'*60}")
    client = NSEXBRLClient(debug_dir=os.path.join(BASE_DATA_DIR, "debug"))

    quarterly_pnl = client.fetch_quarterly_pnl(symbol)
    annual_pnl    = client.fetch_annual_pnl(symbol)

    # FIX 6: Only apply strict filter when explicitly requested
    if target_fy:
        fy25_quarters = _quarters_for_fy(target_fy)
        quarterly_pnl = {k: v for k, v in quarterly_pnl.items() if k in fy25_quarters}
        annual_pnl    = {k: v for k, v in annual_pnl.items()    if k == target_fy}
        logger.info(f"Filtered to {target_fy}: {len(quarterly_pnl)} quarterly | {len(annual_pnl)} annual")
    else:
        # Keep all data (up to 8 quarters, 5 annual years)
        q_sorted = sorted(quarterly_pnl.keys(), reverse=True)[:8]
        a_sorted = sorted(annual_pnl.keys(),    reverse=True)[:5]
        quarterly_pnl = {k: quarterly_pnl[k] for k in q_sorted}
        annual_pnl    = {k: annual_pnl[k]    for k in a_sorted}

    q_ok = sum(1 for v in quarterly_pnl.values() if v.get("revenue_from_operations"))
    a_ok = sum(1 for v in annual_pnl.values()    if v.get("revenue_from_operations"))
    logger.info(f"XBRL result: {q_ok} quarterly | {a_ok} annual periods with revenue data")

    return {"quarterly_pnl": quarterly_pnl, "annual_pnl": annual_pnl}


def _quarters_for_fy(fy: str) -> List[str]:
    """Return the 4 quarter labels that belong to an Indian FY."""
    try:
        yr = int(fy.replace("FY", ""))
        return [f"Jun {yr-1}", f"Sep {yr-1}", f"Dec {yr-1}", f"Mar {yr}"]
    except ValueError:
        return []


# ── Step 2: Download PDFs ──────────────────────────────────────────────────────

def step_download(symbol: str, only_quarterly: bool, only_annual: bool) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 2: PDF DOWNLOAD — {symbol}\n{'#'*60}")
    manifest = download_all_pdfs(
        symbol          = symbol,
        base_dir        = BASE_DATA_DIR,
        quarterly_from  = "01-07-2024",
        quarterly_to    = "31-05-2025",
        annual_from_nse = "01-04-2025",
        annual_to_nse   = "31-12-2025",
        annual_from_bse = "01/04/2025",
        annual_to_bse   = "31/12/2025",
    )
    if only_quarterly:
        manifest["annual"] = []
    if only_annual:
        manifest["quarterly"] = []
    return manifest


def scan_existing_pdfs(symbol: str) -> Dict:
    """Build manifest from already-downloaded PDFs."""
    q_dir = Path(BASE_DATA_DIR) / symbol.upper() / "raw" / "quarterly_pdf"
    a_dir = Path(BASE_DATA_DIR) / symbol.upper() / "raw" / "annual_pdf"
    manifest = {"quarterly": [], "annual": []}

    if q_dir.exists():
        for pdf in sorted(q_dir.glob("*.pdf")):
            label = pdf.stem.replace("_", " ")
            manifest["quarterly"].append({
                "label": label, "path": str(pdf),
                "status": "success", "url": "", "date": "", "source": "disk",
            })

    if a_dir.exists():
        for pdf in sorted(a_dir.glob("*.pdf")):
            label = pdf.stem.split("_")[0]
            manifest["annual"].append({
                "label": label, "path": str(pdf),
                "status": "success", "url": "", "date": "", "source": "disk",
            })

    logger.info(f"Existing PDFs: {len(manifest['quarterly'])} quarterly | {len(manifest['annual'])} annual")
    return manifest


# ── Step 3: Parse + Extract ────────────────────────────────────────────────────

def step_parse_extract(
    symbol:     str,
    manifest:   Dict,
    xbrl_data:  Dict,
    target_fy:  Optional[str] = None,
) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 3: PARSE & EXTRACT — {symbol}\n{'#'*60}")

    parser    = PDFParser()
    extractor = FinancialExtractor()

    quarterly_pnl  = dict(xbrl_data.get("quarterly_pnl", {}))
    annual_pnl     = dict(xbrl_data.get("annual_pnl",    {}))
    balance_sheet: Dict = {}
    cash_flow:     Dict = {}
    data_sources        = []

    # ── Quarterly PDFs: only if XBRL gave us nothing ──────────────────────────
    q_ok = sum(1 for v in quarterly_pnl.values() if v.get("revenue_from_operations"))
    if q_ok == 0:
        logger.info("XBRL quarterly empty — falling back to PDF extraction")
        for item in manifest.get("quarterly", []):
            if item.get("status") != "success" or not item.get("path"):
                continue
            path  = item["path"]
            label = item.get("label", "Unknown")
            logger.info(f"  Quarterly PDF: [{label}] {path}")
            try:
                pr = parser.parse(path)
                if pr["num_pages"] == 0:
                    continue
                q_data = extractor.extract_quarterly_pnl_from_pdf(pr)
                quarterly_pnl.update(q_data)
                logger.info(f"  PDF extracted {len(q_data)} quarterly period(s)")
                data_sources.append({
                    "type": "Quarterly PDF (fallback)", "label": label,
                    "path": path, "url": item.get("url", ""), "source": "NSE",
                })
            except Exception as e:
                logger.error(f"  Error in quarterly PDF {path}: {e}", exc_info=True)
    else:
        logger.info(f"XBRL has {q_ok} quarterly periods — skipping quarterly PDFs")
        for item in manifest.get("quarterly", []):
            if item.get("status") == "success":
                data_sources.append({
                    "type": "Quarterly PDF", "label": item.get("label", ""),
                    "path": item.get("path", ""), "url": item.get("url", ""),
                    "source": "XBRL+NSE",
                })

    # ── Annual PDFs: BS + CF ───────────────────────────────────────────────────
    for item in manifest.get("annual", []):
        if item.get("status") != "success" or not item.get("path"):
            continue
        path  = item["path"]
        label = item.get("label", "FY_Unknown")
        logger.info(f"\n  Annual PDF [{label}]: {path}")
        try:
            pr = parser.parse(path)
            if pr["num_pages"] == 0:
                logger.warning(f"  Empty parse: {path}")
                continue
            logger.info(f"  Parsed {pr['num_pages']} pages")

            bs_data = extractor.extract_balance_sheet(pr)
            if bs_data:
                balance_sheet.update(bs_data)
                logger.info(f"  BS extracted: {list(bs_data.keys())}")
            else:
                logger.warning(f"  No BS data from {label}")

            cf_data = extractor.extract_cash_flow(pr)
            if cf_data:
                cash_flow.update(cf_data)
                logger.info(f"  CF extracted: {list(cf_data.keys())}")
            else:
                logger.warning(f"  No CF data from {label}")

            a_ok2 = sum(1 for v in annual_pnl.values() if v.get("revenue_from_operations"))
            if a_ok2 == 0:
                a_data = extractor.extract_annual_pnl_from_pdf(pr)
                if a_data:
                    annual_pnl.update(a_data)

            data_sources.append({
                "type": "Annual Report PDF", "label": label,
                "path": path, "url": item.get("url", ""),
                "date": item.get("date", ""), "source": item.get("source", "BSE"),
            })

        except Exception as e:
            logger.error(f"  Error in annual PDF {path}: {e}", exc_info=True)

    # FIX 6: Apply filter ONLY if target_fy is specified
    if target_fy:
        fy25_quarters = _quarters_for_fy(target_fy)
        quarterly_pnl = {k: v for k, v in quarterly_pnl.items() if k in fy25_quarters}
        annual_pnl    = {k: v for k, v in annual_pnl.items()    if k == target_fy}
        balance_sheet = {k: v for k, v in balance_sheet.items() if k == target_fy}
        cash_flow     = {k: v for k, v in cash_flow.items()     if k == target_fy}
    else:
        # Keep recent 8 quarters and 5 annual years
        q_sorted = sorted(quarterly_pnl.keys(), reverse=True)[:8]
        a_sorted = sorted(annual_pnl.keys(),    reverse=True)[:5]
        bs_sorted = sorted(balance_sheet.keys(), reverse=True)[:5]
        cf_sorted = sorted(cash_flow.keys(),     reverse=True)[:5]
        quarterly_pnl = {k: quarterly_pnl[k] for k in q_sorted}
        annual_pnl    = {k: annual_pnl[k]    for k in a_sorted}
        balance_sheet = {k: balance_sheet[k]  for k in bs_sorted}
        cash_flow     = {k: cash_flow[k]      for k in cf_sorted}

    logger.info(
        f"\nExtraction complete: "
        f"Q P&L={len(quarterly_pnl)} | "
        f"A P&L={len(annual_pnl)} | "
        f"BS={len(balance_sheet)} | "
        f"CF={len(cash_flow)}"
    )

    return {
        "quarterly_pnl": quarterly_pnl,
        "annual_pnl":    annual_pnl,
        "balance_sheet": balance_sheet,
        "cash_flow":     cash_flow,
        "data_sources":  data_sources,
    }


# ── Step 4: Validate ───────────────────────────────────────────────────────────

def step_validate(symbol: str, extracted: Dict) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 4: VALIDATE — {symbol}\n{'#'*60}")
    company_data = {
        "company_info": {"ticker": symbol},
        "profit_loss": {
            "quarterly": extracted["quarterly_pnl"],
            "yearly":    extracted["annual_pnl"],
        },
        "balance_sheet": {"yearly": extracted["balance_sheet"]},
        "cash_flow":     {"yearly": extracted["cash_flow"]},
    }
    return validate_all(company_data)


# ── Step 5: Export ─────────────────────────────────────────────────────────────

def step_export(symbol: str, extracted: Dict, validation: Dict) -> str:
    logger.info(f"\n{'#'*60}\nSTEP 5: EXPORT — {symbol}\n{'#'*60}")

    info         = COMPANY_REGISTRY.get(symbol, {})
    company_name = info.get("name", symbol)

    output = build_output(
        symbol        = symbol,
        company_name  = company_name,
        quarterly_pnl = extracted["quarterly_pnl"],
        annual_pnl    = extracted["annual_pnl"],
        balance_sheet = extracted["balance_sheet"],
        cash_flow     = extracted["cash_flow"],
        validation    = validation,
        data_sources  = extracted["data_sources"],
    )

    final_dir     = os.path.join(BASE_DATA_DIR, symbol.upper(), "final")
    extracted_dir = os.path.join(BASE_DATA_DIR, symbol.upper(), "extracted")
    os.makedirs(final_dir,     exist_ok=True)
    os.makedirs(extracted_dir, exist_ok=True)

    save_json(extracted["quarterly_pnl"], os.path.join(extracted_dir, "profit_loss_quarterly.json"))
    save_json(extracted["annual_pnl"],    os.path.join(extracted_dir, "profit_loss_yearly.json"))
    save_json(extracted["balance_sheet"], os.path.join(extracted_dir, "balance_sheet.json"))
    save_json(extracted["cash_flow"],     os.path.join(extracted_dir, "cash_flow.json"))

    canonical = os.path.join(final_dir, "company_financials.json")
    save_json(output, canonical)
    print_summary(output)
    return canonical


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Financial Pipeline v3 (Fixed)")
    p.add_argument("--symbol",          default=DEFAULT_SYMBOL)
    p.add_argument("--skip-download",   action="store_true")
    p.add_argument("--skip-xbrl",       action="store_true")
    p.add_argument("--only-quarterly",  action="store_true")
    p.add_argument("--only-annual",     action="store_true")
    p.add_argument("--skip-validation", action="store_true")
    p.add_argument(
        "--target-fy",
        default=None,
        help="Strict single-FY filter, e.g. FY2025. Default: keep recent 5 years."
    )
    return p.parse_args()


def run_pipeline(
    symbol:          str  = DEFAULT_SYMBOL,
    skip_download:   bool = False,
    skip_xbrl:       bool = False,
    only_quarterly:  bool = False,
    only_annual:     bool = False,
    skip_validation: bool = False,
    target_fy:       Optional[str] = None,
) -> str:
    logger.info(
        f"\n{'#'*60}\n  FINANCIAL PIPELINE v3 (Fixed) — {symbol}\n"
        f"  {datetime.now(timezone.utc).isoformat()}\n{'#'*60}"
    )

    if symbol not in COMPANY_REGISTRY:
        logger.error(f"'{symbol}' not in COMPANY_REGISTRY. Add it to pipeline/downloader.py")
        sys.exit(1)

    xbrl_data = step_xbrl(symbol, target_fy) if not skip_xbrl else {"quarterly_pnl": {}, "annual_pnl": {}}

    if skip_download:
        manifest = scan_existing_pdfs(symbol)
    else:
        manifest = step_download(symbol, only_quarterly, only_annual)

    raw_dir = os.path.join(BASE_DATA_DIR, symbol.upper(), "raw")
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, "download_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    extracted  = step_parse_extract(symbol, manifest, xbrl_data, target_fy)
    validation = step_validate(symbol, extracted) if not skip_validation else {}
    out        = step_export(symbol, extracted, validation)

    logger.info(f"\n✓ Done! Output → {out}")
    logger.info(f"  Log → {_log_file}")
    return out


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(
        symbol          = args.symbol.upper(),
        skip_download   = args.skip_download,
        skip_xbrl       = args.skip_xbrl,
        only_quarterly  = args.only_quarterly,
        only_annual     = args.only_annual,
        skip_validation = args.skip_validation,
        target_fy       = args.target_fy,
    )
