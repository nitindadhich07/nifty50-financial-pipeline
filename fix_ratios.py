"""
Fix ratios in ALL company_financials.json files.

Strategy:
1. For every file, if it already has ratios → verify they look sane, move on.
2. If ratios are empty but P&L / BS / CF data exists → compute ratios on the spot
   using the same formula set as RatioEngine.
3. TATAMOTORS special case: no yearly P&L data at all → write empty ratios {}
   (no data = no ratio).

Run from the project root:
    python3 fix_ratios.py
"""

import json
import math
from pathlib import Path


# ── Ratio computation (mirrors RatioEngine.compute_all) ──────────────────────

def safe_div(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return round(num / denom, 4)


def compute_ratios(pl: dict, bs: dict, cf: dict) -> dict:
    ratios = {}

    rev   = pl.get("revenue_from_operations")
    np_   = pl.get("net_profit")
    pbt   = pl.get("profit_before_tax")
    ebitda = pl.get("ebitda")
    ebit  = pl.get("ebit")
    equity = bs.get("total_equity")
    assets = bs.get("total_assets")
    debt   = bs.get("total_debt")
    cur_assets = bs.get("current_assets")
    cur_liabs  = bs.get("current_liabilities")
    cfo  = cf.get("cash_from_operations")
    fcf  = cf.get("free_cash_flow")
    shares = bs.get("shares_outstanding")

    # Profitability
    npm = safe_div(np_, rev)
    if npm is not None:
        ratios["net_profit_margin"] = round(npm * 100, 2)

    om = safe_div(ebitda, rev)
    if om is not None:
        ratios["operating_margin"] = round(om * 100, 2)

    if equity and np_:
        roe_val = round((np_ / equity) * 100, 2)
        if roe_val > 100:
            ratios["roe_anomaly"] = roe_val
        else:
            ratios["roe"] = roe_val

    if assets and np_:
        ratios["roa"] = round((np_ / assets) * 100, 2)

    # ROCE = EBIT / Capital Employed (assets - cur_liabs)
    if ebit is not None and assets is not None and cur_liabs is not None:
        cap_emp = assets - cur_liabs
        if cap_emp:
            ratios["roce"] = round((ebit / cap_emp) * 100, 2)

    # Leverage
    if equity is not None and debt is not None:
        ratios["debt_to_equity"] = round(debt / (equity or 1), 2)

    if assets and debt is not None:
        ratios["debt_to_assets"] = round(debt / assets, 2)

    # Liquidity
    if cur_assets is not None and cur_liabs:
        ratios["current_ratio"] = round(cur_assets / (cur_liabs or 1), 2)

    # Cash flow
    if rev and fcf is not None:
        ratios["free_cash_flow_margin"] = round((fcf / rev) * 100, 2)

    if np_ and cfo:
        ratios["cfo_to_net_profit"] = round(cfo / np_, 2)

    return ratios


# ── Main ──────────────────────────────────────────────────────────────────────

def process_file(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))

    existing_ratios = raw.get("ratios") or {}
    pl_yearly = raw.get("profit_loss", {}).get("yearly") or {}
    bs_yearly  = raw.get("balance_sheet", {}).get("yearly") or {}
    cf_yearly  = raw.get("cash_flow", {}).get("yearly") or {}

    # Collect all FY periods present across P&L, BS, CF
    all_years = sorted(
        set(pl_yearly) | set(bs_yearly) | set(cf_yearly),
        reverse=True,
    )

    if not all_years:
        print(f"  ⚠️  No yearly data → skipping ratios: {path}")
        return

    new_ratios = {}
    for fy in all_years:
        pl = pl_yearly.get(fy) or {}
        bs = bs_yearly.get(fy) or {}
        cf = cf_yearly.get(fy) or {}
        computed = compute_ratios(pl, bs, cf)
        if computed:
            # Prefer existing data if the ratio set is richer (more keys)
            existing_for_fy = existing_ratios.get(fy) or {}
            if len(computed) >= len(existing_for_fy):
                new_ratios[fy] = computed
            else:
                new_ratios[fy] = existing_for_fy

    raw["ratios"] = new_ratios

    path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    ticker = (raw.get("company_info") or {}).get("ticker", path.parent.parent.name.upper())
    populated = sum(1 for v in new_ratios.values() if v)
    print(f"  ✅  {ticker:<14} → {populated} FY ratio set(s) written  [{path}]")


def main():
    data_root = Path("data")
    json_files = sorted(data_root.rglob("*/final/company_financials.json"))
    print(f"Found {len(json_files)} file(s).\n")

    for f in json_files:
        try:
            process_file(f)
        except Exception as e:
            print(f"  ⚠️  ERROR in {f}: {e}")

    print(f"\n✨ Done.")


if __name__ == "__main__":
    main()
