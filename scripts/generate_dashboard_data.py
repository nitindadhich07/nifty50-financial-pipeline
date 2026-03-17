import os
import json
from pathlib import Path

def generate_dashboard():
    base_dir = Path("data")
    dashboard_dir = Path("dashboard")
    dashboard_dir.mkdir(exist_ok=True)
    
    data_output_dir = dashboard_dir / "data"
    data_output_dir.mkdir(exist_ok=True)
    
    # Store candidates: { symbol: [ (quality_score, path, data) ] }
    candidates = {}
    
    print(f"Scanning {base_dir} for company_financials.json...")
    
    for path in base_dir.rglob("company_financials.json"):
        if "dashboard" in str(path): continue
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            # Simple quality score: how many quarterly + annual periods are present?
            pl = data.get("profit_loss", {})
            q_count = len(pl.get("quarterly", {}))
            a_count = len(pl.get("yearly", {})) + len(pl.get("annual", {}))
            score = q_count + a_count
            
            # Prefer 'standard' or 'bank' paths if scores are tied
            boost = 0
            if "standard" in str(path) or "bank" in str(path) or "insurance" in str(path) or "utility" in str(path):
                boost = 10
            
            total_score = score + boost
            
            info = data.get("company_info", {})
            symbol = info.get("symbol")
            if not symbol:
                symbol = path.parent.parent.name.upper()
            
            symbol = symbol.upper()
            if symbol not in candidates:
                candidates[symbol] = []
            
            candidates[symbol].append((total_score, path, data))
            
        except Exception as e:
            print(f"Error reading {path}: {e}")

    final_companies = []
    for symbol, options in candidates.items():
        # Pick the one with the highest quality score
        options.sort(key=lambda x: x[0], reverse=True)
        best_score, best_path, best_data = options[0]
        
        print(f"Picking {best_path} for {symbol} (Score: {best_score})")
        
        # Save to dashboard/data
        with open(data_output_dir / f"{symbol}.json", 'w') as f:
            json.dump(best_data, f)
            
        final_companies.append({
            "symbol": symbol,
            "name": best_data.get("company_info", {}).get("name", symbol),
            "sector": best_data.get("company_info", {}).get("sector", "Diversified"),
            "path": str(best_path)
        })

    # Sort and save index
    final_companies.sort(key=lambda x: x["symbol"])
    with open(dashboard_dir / "companies.json", 'w') as f:
        json.dump(final_companies, f, indent=2)
        
    print(f"Generated dashboard data for {len(final_companies)} companies.")

if __name__ == "__main__":
    generate_dashboard()
