from typing import Dict, Literal

# Common sector mapping for NIFTY 50
SECTOR_MAP: Dict[str, Literal["bank", "nbfc", "insurance", "utility", "standard"]] = {
    "HDFCBANK": "bank",
    "ICICIBANK": "bank",
    "SBIN": "bank",
    "AXISBANK": "bank",
    "KOTAKBANK": "bank",
    "INDUSINDBK": "bank",
    
    "BAJFINANCE": "nbfc",
    "BAJAJFINSV": "nbfc",
    
    "HDFCLIFE": "insurance",
    "SBILIFE": "insurance",

    "NTPC": "utility",
    "POWERGRID": "utility",

    # Remaining companies default to "standard"
}

def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.upper(), "standard")
