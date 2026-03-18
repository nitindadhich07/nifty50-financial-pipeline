"""
app.py — Minimal FastAPI Backend (Phase A)
==========================================
Replaces: python3 -m http.server 8080
Run with: uvicorn app:app --host 0.0.0.0 --port 8080 --reload

Architecture:
  - Serves dashboard/ as static files (same as before)
  - Adds /api/* endpoints for dynamic queries
  - File-based caching with 24h TTL (no Redis required)
  - /api/chat is a Phase B stub (returns 503 until LLM is wired)

Install: pip install fastapi uvicorn[standard]
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("NiftyAPI")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
DASHBOARD_DIR = ROOT.parent / "dashboard"
DATA_DIR      = DASHBOARD_DIR / "data"
CACHE_DIR     = ROOT.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_HOURS = 24

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Nifty50 Financial Intelligence API",
    description="Zero-hallucination financial data from NSE/BSE official filings only.",
    version="3.1-PhaseA",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── Cache helpers ────────────────────────────────────────────────────────────
def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"

def _cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    return age < timedelta(hours=CACHE_TTL_HOURS)

def _read_cache(key: str) -> Optional[Dict]:
    p = _cache_path(key)
    if _cache_valid(p):
        with open(p) as f:
            return json.load(f)
    return None

def _write_cache(key: str, data: Any):
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


# ─── Data loader ──────────────────────────────────────────────────────────────
def _load_company(symbol: str) -> Dict:
    path = DATA_DIR / f"{symbol.upper()}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Company {symbol} not found")
    with open(path) as f:
        return json.load(f)


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/companies")
def list_companies():
    """List all available companies."""
    companies_path = DASHBOARD_DIR / "companies.json"
    if not companies_path.exists():
        raise HTTPException(status_code=503, detail="Companies index not found. Run generate_dashboard_data.py first.")
    with open(companies_path) as f:
        return json.load(f)


@app.get("/api/financials/{symbol}")
def get_financials(
    symbol: str,
    type:   str = Query("consolidated", enum=["consolidated", "standalone"]),
    period: str = Query("annual",       enum=["annual", "quarterly"]),
):
    """
    Returns financial statements for a company.
    type=consolidated (default) | standalone
    period=annual | quarterly

    NOTE: quarterly only returns profit_loss (Balance Sheet and Cash Flow are
    annual-only under Indian GAAP filings).
    """
    cache_key = f"financials_{symbol}_{type}_{period}"
    cached = _read_cache(cache_key)
    if cached:
        return cached

    data = _load_company(symbol)
    financials = data.get("financials", {})
    fin_type = financials.get(type)

    if fin_type is None:
        if type == "standalone":
            return JSONResponse(
                status_code=200,
                content={
                    "available": False,
                    "message": "Standalone filing not separately fetched. Only consolidated data available.",
                    "symbol": symbol.upper(),
                }
            )
        raise HTTPException(status_code=404, detail="Financial data not found")

    if period == "quarterly":
        result = {
            "profit_loss": fin_type.get("quarterly", {}).get("profit_loss", {}),
            "note": "Balance Sheet and Cash Flow are not available in quarterly view (annual filing only under Indian GAAP)"
        }
    else:
        result = fin_type.get("annual", {})

    _write_cache(cache_key, result)
    return result


@app.get("/api/metrics/{symbol}")
def get_derived_metrics(symbol: str):
    """Returns pre-computed derived metrics (ROE, D/E, margins, growth %)."""
    cache_key = f"metrics_{symbol}"
    cached = _read_cache(cache_key)
    if cached:
        return cached

    data = _load_company(symbol)
    result = {
        "derived_metrics": data.get("derived_metrics", {}),
        "confidence":      data.get("confidence", {}).get("derived_metrics", {}),
        "source":          "DERIVED_FROM_FILINGS",
    }
    _write_cache(cache_key, result)
    return result


@app.get("/api/graph/{symbol}")
def get_graph_data(
    symbol: str,
    metric: str = Query("revenue", description="Metric key: revenue, net_profit, ebitda, eps, total_debt, ..."),
    period: str = Query("quarterly", enum=["quarterly", "annual"]),
):
    """
    Returns time-series data for charting.
    Rules: No interpolation. Minimum 2 points. Missing periods skipped.
    """
    data     = _load_company(symbol)
    gd       = data.get("graph_data", {})
    metric_d = gd.get(metric)

    if metric_d is None:
        return {
            "available": False,
            "message":   f"Metric '{metric}' has insufficient filing data for charting (requires ≥2 data points).",
        }

    series = metric_d.get(period, [])
    if len(series) < 2:
        alt = "annual" if period == "quarterly" else "quarterly"
        series = metric_d.get(alt, [])
        if len(series) < 2:
            return {
                "available": False,
                "message":   "Insufficient data points for chart rendering.",
            }

    return {
        "available": True,
        "metric":    metric,
        "label":     metric_d.get("label", metric),
        "unit":      metric_d.get("unit", "₹ Crores"),
        "period":    period,
        "series":    series,
        "note":      "No interpolation applied. Missing periods are skipped.",
    }


@app.get("/api/insights/{symbol}")
def get_insights(symbol: str):
    """Returns rule-based insights and anomaly flags."""
    data = _load_company(symbol)
    return {
        "insights":  data.get("insights",  []),
        "anomalies": data.get("anomalies", []),
        "note":      "Rule-based only. LLM insights available in Phase B.",
    }


@app.post("/api/chat")
def chat(symbol: str, query: str):
    """
    [Phase B Stub] Live financial Q&A via Groq API.
    Returns 503 until LLM router is wired in Phase B.
    """
    return JSONResponse(
        status_code=503,
        content={
            "status":  "Phase B feature",
            "message": "Chat endpoint requires LLM_BACKEND=groq or LLM_BACKEND=ollama. "
                       "Set GROQ_API_KEY and restart to enable.",
            "phase":   "B",
        },
    )


@app.get("/api/health")
def health():
    return {
        "status":   "ok",
        "version":  "3.1-PhaseA",
        "llm":      os.getenv("LLM_BACKEND", "disabled"),
        "cache_dir": str(CACHE_DIR),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Static File Serving (dashboard/) ─────────────────────────────────────────
# Mount AFTER API routes so /api/* takes priority
app.mount("/", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")
