import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Company:
    symbol: str
    name: Optional[str] = None
    scrip_code: Optional[str] = None  # BSE
    cin: Optional[str] = None  # MCA
    isin: Optional[str] = None
    ir_urls: Optional[List[str]] = None
    sector: Optional[str] = None
    industry: Optional[str] = None


def load_universe(path: str = "pipeline_v3/config/nifty50_universe.json") -> List[Company]:
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        companies = []
        for c in data.get("companies", []):
            companies.append(
                Company(
                    symbol=str(c.get("symbol", "")).upper(),
                    name=c.get("name"),
                    scrip_code=c.get("scrip_code"),
                    cin=c.get("cin"),
                    isin=c.get("isin"),
                    ir_urls=c.get("ir_urls") or [],
                    sector=c.get("sector"),
                    industry=c.get("industry"),
                )
            )
        return [c for c in companies if c.symbol]

    # Fallback to legacy registry if present.
    try:
        from pipeline.downloader import COMPANY_REGISTRY  # type: ignore

        out: List[Company] = []
        for sym, meta in COMPANY_REGISTRY.items():
            out.append(
                Company(
                    symbol=sym.upper(),
                    name=meta.get("name"),
                    scrip_code=meta.get("scrip_code"),
                    isin=meta.get("isin"),
                    cin=None,
                    ir_urls=[],
                )
            )
        return out
    except Exception:
        return []

