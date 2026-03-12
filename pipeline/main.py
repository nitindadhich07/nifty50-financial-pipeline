#!/usr/bin/env python3
"""
main.py  (PRODUCTION v4)
=========================
Pipeline orchestration. Key fixes over v3:

FIX A: Annual P&L now sourced from consolidated annual report PDF
       (XBRL "Annual" period returns standalone quarterly snapshots, not
       full-year consolidated figures).

FIX B: Quarterly P&L from XBRL is correctly labeled and retained even when
       aggregating to annual; no FY2025-only hard filter.

FIX C: Annual PDF validation — press releases are rejected (<5 MB, <10 pages).

FIX D: Quarterly aggregation fallback: when PDF annual P&L extraction fails,
       4 quarters are summed to derive annual.

Usage:
  python3 -m pipeline.main
  python3 -m pipeline.main --symbol RELIANCE
  python3 -m pipeline.main --skip-download   (use already-downloaded PDFs)
  python3 -m pipeline.main --target-fy FY2025
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

# Minimum sizes to consider a PDF as a genuine annual report (not a press release)
MIN_ANNUAL_PDF_MB    = 3.0
MIN_ANNUAL_PDF_PAGES = 50


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: XBRL — Quarterly P&L
# ─────────────────────────────────────────────────────────────────────────────

def step_xbrl(symbol: str) -> Dict:
    """
    Fetch quarterly P&L from NSE XBRL API.
    NOTE: The "Annual" XBRL endpoint returns standalone quarterly snapshots,
    NOT full-year consolidated figures. Annual P&L is sourced from the PDF.
    """
    logger.info(f"\n{'#'*60}\nSTEP 1: NSE XBRL (Quarterly P&L) — {symbol}\n{'#'*60}")
    client = NSEXBRLClient(debug_dir=os.path.join(BASE_DATA_DIR, "debug"))

    quarterly_pnl = client.fetch_quarterly_pnl(symbol)

    q_ok = sum(1 for v in quarterly_pnl.values() if v.get("revenue_from_operations"))
    logger.info(f"XBRL: {q_ok}/{len(quarterly_pnl)} quarters with revenue data")

    # Keep recent 8 quarters
    q_sorted = sorted(quarterly_pnl.keys(), reverse=True)[:8]
    quarterly_pnl = {k: quarterly_pnl[k] for k in q_sorted}

    return {"quarterly_pnl": quarterly_pnl}


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Download PDFs
# ─────────────────────────────────────────────────────────────────────────────

def step_download(symbol: str, only_quarterly: bool = False, only_annual: bool = False) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 2: PDF DOWNLOAD — {symbol}\n{'#'*60}")
    manifest = download_all_pdfs(
        symbol          = symbol,
        base_dir        = BASE_DATA_DIR,
        quarterly_from  = "01-04-2024",
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
                "label": label, "path": str(pdf), "status": "success",
                "url": "", "date": "", "source": "disk",
            })

    if a_dir.exists():
        for pdf in sorted(a_dir.glob("*.pdf")):
            label = pdf.stem.split("_")[0]
            size_mb = pdf.stat().st_size / (1024 * 1024)
            manifest["annual"].append({
                "label": label, "path": str(pdf), "status": "success",
                "url": "", "date": "", "source": "disk",
                "size_mb": round(size_mb, 1),
            })
            logger.info(f"  Found annual PDF: {pdf.name} ({size_mb:.1f} MB)")

    logger.info(f"Existing PDFs: {len(manifest['quarterly'])} quarterly | {len(manifest['annual'])} annual")
    return manifest


def _is_valid_annual_pdf(path: str, parser: PDFParser) -> bool:
    """
    Validate that a PDF is a genuine annual report (not a press release or notice).
    Checks file size AND page count.
    """
    if not path or not os.path.exists(path):
        return False

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb < MIN_ANNUAL_PDF_MB:
        logger.warning(f"  PDF too small ({size_mb:.1f} MB < {MIN_ANNUAL_PDF_MB} MB) — likely a press release")
        return False

    try:
        pr = parser.parse(path)
        if pr["num_pages"] < MIN_ANNUAL_PDF_PAGES:
            logger.warning(f"  PDF too few pages ({pr['num_pages']} < {MIN_ANNUAL_PDF_PAGES}) — not a full annual report")
            return False
    except Exception as e:
        logger.warning(f"  Could not parse PDF for validation: {e}")
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Parse + Extract
# ─────────────────────────────────────────────────────────────────────────────

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
    annual_pnl:    Dict = {}
    balance_sheet: Dict = {}
    cash_flow:     Dict = {}
    data_sources        = []

    # Record quarterly PDFs as data sources
    for item in manifest.get("quarterly", []):
        if item.get("status") == "success":
            data_sources.append({
                "type": "Quarterly P&L", "label": item.get("label", ""),
                "path": item.get("path", ""), "url": item.get("url", ""),
                "source": "NSE XBRL (Standalone)",
            })

    # ── Quarterly PDF fallback (only if XBRL empty) ───────────────────────
    q_ok = sum(1 for v in quarterly_pnl.values() if v.get("revenue_from_operations"))
    if q_ok == 0:
        logger.info("XBRL quarterly empty — falling back to quarterly PDF extraction")
        for item in manifest.get("quarterly", []):
            if item.get("status") != "success" or not item.get("path"):
                continue
            try:
                pr = parser.parse(item["path"])
                if pr["num_pages"] == 0:
                    continue
                q_data = extractor.extract_quarterly_pnl_from_pdf(pr)
                quarterly_pnl.update(q_data)
                logger.info(f"  PDF extracted {len(q_data)} quarterly period(s)")
                data_sources.append({"type": "Quarterly PDF (fallback)", "label": item.get("label"),
                                      "path": item["path"], "source": "NSE PDF"})
            except Exception as e:
                logger.error(f"  Quarterly PDF error: {e}", exc_info=True)

    # ── Annual PDFs: BS, CF, and Consolidated P&L ─────────────────────────
    for item in manifest.get("annual", []):
        if item.get("status") != "success" or not item.get("path"):
            continue
        path  = item["path"]
        label = item.get("label", "FY_Unknown")

        # FIX C: Reject press releases / notices
        if not _is_valid_annual_pdf(path, parser):
            logger.warning(f"  Annual PDF [{label}] rejected — not a full annual report. "
                            f"Run with --skip-download after placing the full 400+ page annual report in "
                            f"data/{symbol.upper()}/raw/annual_pdf/{label}_Annual_Report.pdf")
            continue

        logger.info(f"\n  Processing annual PDF [{label}]: {path}")
        try:
            pr = parser.parse(path)
            logger.info(f"  Parsed {pr['num_pages']} pages")

            # Balance Sheet
            bs_data = extractor.extract_balance_sheet(pr)
            if bs_data:
                balance_sheet.update(bs_data)
                logger.info(f"  BS: {list(bs_data.keys())}")

            # Cash Flow
            cf_data = extractor.extract_cash_flow(pr)
            if cf_data:
                cash_flow.update(cf_data)
                logger.info(f"  CF: {list(cf_data.keys())}")

            # Annual P&L (consolidated) — preferred over XBRL aggregate
            pnl_data = extractor.extract_annual_pnl_from_pdf(pr)
            if pnl_data and any(v.get("revenue_from_operations") for v in pnl_data.values()):
                annual_pnl.update(pnl_data)
                logger.info(f"  Annual P&L (PDF): {list(pnl_data.keys())}")
            else:
                logger.warning(f"  Annual P&L extraction empty for {label}")

            data_sources.append({
                "type": "Annual Report PDF (Consolidated)", "label": label,
                "path": path, "url": item.get("url", ""),
                "date": item.get("date", ""), "source": item.get("source", "BSE"),
                "pages": pr["num_pages"],
            })

        except Exception as e:
            logger.error(f"  Annual PDF error: {e}", exc_info=True)

    # FIX D: Aggregate quarters → annual if PDF extraction failed
    if not annual_pnl or not any(v.get("revenue_from_operations") for v in annual_pnl.values()):
        logger.info("Annual P&L not available from PDF — aggregating from quarterly XBRL...")
        annual_pnl = _aggregate_quarters_to_annual(quarterly_pnl)

    # Optional target-FY filter
    if target_fy:
        fy_quarters = _quarters_for_fy(target_fy)
        quarterly_pnl = {k: v for k, v in quarterly_pnl.items() if k in fy_quarters}
        annual_pnl    = {k: v for k, v in annual_pnl.items()    if k == target_fy}
        balance_sheet = {k: v for k, v in balance_sheet.items() if k == target_fy}
        cash_flow     = {k: v for k, v in cash_flow.items()     if k == target_fy}
    else:
        # Keep recent 8 quarters, 5 annual years
        quarterly_pnl = {k: quarterly_pnl[k] for k in sorted(quarterly_pnl)[::-1][:8]}
        annual_pnl    = {k: annual_pnl[k]    for k in sorted(annual_pnl)[::-1][:5]}
        balance_sheet = {k: balance_sheet[k]  for k in sorted(balance_sheet)[::-1][:5]}
        cash_flow     = {k: cash_flow[k]      for k in sorted(cash_flow)[::-1][:5]}

    logger.info(
        f"\nExtraction complete → "
        f"Q P&L={len(quarterly_pnl)} | A P&L={len(annual_pnl)} | "
        f"BS={len(balance_sheet)} | CF={len(cash_flow)}"
    )
    return {
        "quarterly_pnl": quarterly_pnl,
        "annual_pnl":    annual_pnl,
        "balance_sheet": balance_sheet,
        "cash_flow":     cash_flow,
        "data_sources":  data_sources,
    }


def _quarters_for_fy(fy: str) -> List[str]:
    """Indian FY quarter labels: FY2025 → Jun 2024, Sep 2024, Dec 2024, Mar 2025."""
    try:
        yr = int(fy.replace("FY", ""))
        return [f"Jun {yr-1}", f"Sep {yr-1}", f"Dec {yr-1}", f"Mar {yr}"]
    except ValueError:
        return []


def _aggregate_quarters_to_annual(quarterly_pnl: Dict) -> Dict:
    """
    Aggregate 4 quarterly P&L periods into one annual period.
    Additive fields: revenue, expenses, EBITDA, depreciation, interest, PBT, tax, net profit.
    Non-additive: EPS, margins (recomputed from annual totals).
    """
    # Group quarters by FY
    from collections import defaultdict
    fy_groups: Dict[str, List[Dict]] = defaultdict(list)

    for q_label, q_data in quarterly_pnl.items():
        fy = _fy_from_quarter_label(q_label)
        if fy:
            fy_groups[fy].append(q_data)

    annual_pnl = {}
    additive_fields = [
        "revenue_from_operations", "other_income", "total_income",
        "raw_material_cost", "employee_cost", "total_expenses",
        "total_operating_expenses", "ebitda", "depreciation", "interest",
        "profit_before_tax", "tax", "net_profit", "net_profit_after_minority",
    ]

    for fy, quarters in fy_groups.items():
        if len(quarters) < 3:
            logger.warning(f"  FY {fy}: only {len(quarters)} quarters available for aggregation (need 4)")
        if len(quarters) == 0:
            continue

        agg: Dict = {}
        for field in additive_fields:
            vals = [q.get(field) for q in quarters if q.get(field) is not None]
            agg[field] = round(sum(vals), 2) if vals else None

        # Recompute margins
        sales   = agg.get("revenue_from_operations")
        ebitda  = agg.get("ebitda")
        net_p   = agg.get("net_profit")
        depr    = agg.get("depreciation")
        interest = agg.get("interest")
        pbt     = agg.get("profit_before_tax")

        if sales and ebitda and sales != 0:
            agg["ebitda_margin_pct"] = round(ebitda / sales * 100, 2)
        else:
            agg["ebitda_margin_pct"] = None

        # Use latest quarter's EPS (annualized is complex; just note it)
        last_q = quarters[0]  # sorted reverse, so first is latest
        agg["eps"]            = last_q.get("eps")
        agg["eps_diluted"]    = last_q.get("eps_diluted")
        agg["minority_interest"] = None
        agg["power_fuel_cost"]   = None
        agg["selling_general_admin"] = None
        agg["other_expenses"]    = None

        annual_pnl[fy] = agg
        logger.info(
            f"  Aggregated {len(quarters)} quarters → {fy}: "
            f"Revenue={agg.get('revenue_from_operations')}, "
            f"NetProfit={agg.get('net_profit')}"
        )

    return annual_pnl


def _fy_from_quarter_label(label: str) -> Optional[str]:
    """
    "Jun 2024" → "FY2025",  "Sep 2024" → "FY2025",
    "Dec 2024" → "FY2025",  "Mar 2025" → "FY2025"
    """
    import re
    m = re.match(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", label, re.I)
    if not m:
        return None
    month_str = m.group(1).capitalize()
    year      = int(m.group(2))
    month_map = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                 "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}
    month = month_map.get(month_str, 0)
    # Indian FY: Apr–Mar. Quarters in Apr-Mar go to FY ending next March.
    if month in [4, 5, 6]:          # Q1: Apr-Jun
        return f"FY{year + 1}"
    elif month in [7, 8, 9]:        # Q2: Jul-Sep
        return f"FY{year + 1}"
    elif month in [10, 11, 12]:     # Q3: Oct-Dec
        return f"FY{year + 1}"
    else:                           # Q4: Jan-Mar
        return f"FY{year}"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Validate
# ─────────────────────────────────────────────────────────────────────────────

def step_validate(symbol: str, extracted: Dict) -> Dict:
    logger.info(f"\n{'#'*60}\nSTEP 4: VALIDATE — {symbol}\n{'#'*60}")
    return validate_all({
        "company_info":  {"ticker": symbol},
        "profit_loss":   {"quarterly": extracted["quarterly_pnl"], "yearly": extracted["annual_pnl"]},
        "balance_sheet": {"yearly": extracted["balance_sheet"]},
        "cash_flow":     {"yearly": extracted["cash_flow"]},
    })


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5: Export
# ─────────────────────────────────────────────────────────────────────────────

def step_export(symbol: str, extracted: Dict, validation: Dict) -> str:
    logger.info(f"\n{'#'*60}\nSTEP 5: EXPORT — {symbol}\n{'#'*60}")

    info  = COMPANY_REGISTRY.get(symbol, {})
    output = build_output(
        symbol        = symbol,
        company_name  = info.get("name", symbol),
        quarterly_pnl = extracted["quarterly_pnl"],
        annual_pnl    = extracted["annual_pnl"],
        balance_sheet = extracted["balance_sheet"],
        cash_flow     = extracted["cash_flow"],
        validation    = validation,
        data_sources  = extracted["data_sources"],
        sector        = info.get("sector", "Diversified"),
        industry      = info.get("industry", "Conglomerate"),
    )

    final_dir = os.path.join(BASE_DATA_DIR, symbol.upper(), "final")
    ext_dir   = os.path.join(BASE_DATA_DIR, symbol.upper(), "extracted")
    os.makedirs(final_dir, exist_ok=True)
    os.makedirs(ext_dir,   exist_ok=True)

    save_json(extracted["quarterly_pnl"], os.path.join(ext_dir, "profit_loss_quarterly.json"))
    save_json(extracted["annual_pnl"],    os.path.join(ext_dir, "profit_loss_yearly.json"))
    save_json(extracted["balance_sheet"], os.path.join(ext_dir, "balance_sheet.json"))
    save_json(extracted["cash_flow"],     os.path.join(ext_dir, "cash_flow.json"))

    canonical = os.path.join(final_dir, "company_financials.json")
    save_json(output, canonical)
    print_summary(output)
    return canonical


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Financial Pipeline v4")
    p.add_argument("--symbol",          default=DEFAULT_SYMBOL)
    p.add_argument("--skip-download",   action="store_true",
                   help="Use already-downloaded PDFs in data/<SYMBOL>/raw/")
    p.add_argument("--skip-xbrl",       action="store_true")
    p.add_argument("--only-quarterly",  action="store_true")
    p.add_argument("--only-annual",     action="store_true")
    p.add_argument("--skip-validation", action="store_true")
    p.add_argument("--target-fy",       default=None,
                   help="Filter output to single FY, e.g. FY2025")
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
        f"\n{'#'*60}\n  FINANCIAL PIPELINE v4 — {symbol}\n"
        f"  {datetime.now(timezone.utc).isoformat()}\n{'#'*60}"
    )

    if symbol not in COMPANY_REGISTRY:
        logger.error(f"'{symbol}' not in COMPANY_REGISTRY. Add it to pipeline/downloader.py")
        sys.exit(1)

    xbrl_data = step_xbrl(symbol) if not skip_xbrl else {"quarterly_pnl": {}}

    manifest  = scan_existing_pdfs(symbol) if skip_download else step_download(
        symbol, only_quarterly, only_annual
    )

    raw_dir = os.path.join(BASE_DATA_DIR, symbol.upper(), "raw")
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, "download_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    extracted  = step_parse_extract(symbol, manifest, xbrl_data, target_fy)
    validation = step_validate(symbol, extracted) if not skip_validation else {}
    out        = step_export(symbol, extracted, validation)

    logger.info(f"\n✅ Done! Output → {out}")
    logger.info(f"   Log  → {_log_file}")
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
