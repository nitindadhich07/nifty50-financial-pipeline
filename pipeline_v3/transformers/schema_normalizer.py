import logging
import re
from dataclasses import asdict, is_dataclass
from typing import Dict, Any, Optional, Literal, Tuple
from .financial_mapper import ProfitLoss, BalanceSheet, CashFlow, CompanyFinancials

logger = logging.getLogger(__name__)

class SchemaNormalizer:
    """Normalizes raw data into the unified schema with institutional guardrails."""

    DEFAULT_SOURCE_PRIORITY = {
        "MCA_XBRL": 400,
        "BSE_XBRL": 350,
        "NSE_API": 300,
        "BSE_API": 280,
        "IR_TABLE": 200,
        "PDF": 100,
        "UNKNOWN": 0,
    }
    
    def normalize_nse_pnl(self, raw_nse_data: Dict[str, Any]) -> Dict[str, ProfitLoss]:
        normalized = {}
        items = raw_nse_data.get("resCmpData", [])
        for item in items:
            to_dt = item.get("re_to_dt", "")
            label = self._label_from_nse_period(to_dt)
            
            pl = ProfitLoss(
                revenue_from_operations=self._safe_float(item.get("re_net_sale"), divisor=100.0),
                other_income=self._safe_float(item.get("re_oth_inc_new"), divisor=100.0),
                interest=self._safe_float(item.get("re_int_new"), divisor=100.0),
                depreciation=self._safe_float(item.get("re_depr_und_exp"), divisor=100.0),
                profit_before_tax=self._safe_float(item.get("re_pro_loss_bef_tax"), divisor=100.0),
                tax=self._safe_float(item.get("re_tax"), divisor=100.0),
                net_profit=self._safe_float(item.get("re_net_profit"), divisor=100.0),
                eps=self._safe_float(item.get("re_basic_eps"), divisor=1.0)
            )
            self._apply_pnl_math(pl)
            normalized[label] = pl
        return normalized

    def _label_from_nse_period(self, to_dt: str) -> str:
        """
        NSE results-comparision commonly returns a period end like '31-12-2024'.
        Convert to a stable quarter label like 'Q3-FY2025'.
        """
        m = re.search(r"(\d{2})[-/](\d{2})[-/](\d{4})", to_dt)
        if not m:
            y = re.search(r"(\d{4})", to_dt)
            return f"FY{y.group(1)}" if y else "Unknown"
        dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # FY ends March: Apr-Jun Q1, Jul-Sep Q2, Oct-Dec Q3, Jan-Mar Q4
        if mm in (4, 5, 6):
            q = 1
            fy = yyyy + 1
        elif mm in (7, 8, 9):
            q = 2
            fy = yyyy + 1
        elif mm in (10, 11, 12):
            q = 3
            fy = yyyy + 1
        else:
            q = 4
            fy = yyyy
        return f"Q{q}-FY{fy}"

    def normalize_pdf_data(self, raw_pdf_layer: Dict[str, Any]) -> Dict[str, Any]:
        """Maps raw extracted PDF dict (field -> value) to dataclasses."""
        current_raw = raw_pdf_layer.get("current", {})
        prev_raw = raw_pdf_layer.get("prev", {})
        
        def map_to_classes(raw: Dict[str, float]):
            # Rule 4: Inventory Validation
            if "inventory" in raw and raw["inventory"] < 0:
                logger.warning(f"Discarding negative inventory: {raw['inventory']}")
                raw["inventory"] = None

            pl_data = {k: raw[k] for k in ProfitLoss.__dataclass_fields__ if k in raw}
            bs_data = {k: raw[k] for k in BalanceSheet.__dataclass_fields__ if k in raw}
            cf_data = {k: raw[k] for k in CashFlow.__dataclass_fields__ if k in raw}
            
            pl = ProfitLoss(**pl_data)
            bs = BalanceSheet(**bs_data)
            cf = CashFlow(**cf_data)
            
            self._apply_pnl_math(pl)
            self._apply_bs_math(bs)
            self._apply_cf_math(cf)
                 
            return pl, bs, cf

        curr_pl, curr_bs, curr_cf = map_to_classes(current_raw)
        prev_pl, prev_bs, prev_cf = map_to_classes(prev_raw)
        
        return {
            "current": {"pl": curr_pl, "bs": curr_bs, "cf": curr_cf},
            "prev": {"pl": prev_pl, "bs": prev_bs, "cf": prev_cf}
        }

    def normalize_statement_dict(self, statement: Dict[str, Any]) -> Dict[str, Any]:
        """
        Accepts a dict with keys like {"pl": {...}, "bs": {...}, "cf": {...}} and returns
        {"pl": ProfitLoss, "bs": BalanceSheet, "cf": CashFlow}.
        """
        out: Dict[str, Any] = {}
        if "pl" in statement and isinstance(statement["pl"], dict):
            pl_data = {k: self._safe_float(statement["pl"].get(k), divisor=1.0) for k in ProfitLoss.__dataclass_fields__ if k in statement["pl"]}
            pl = ProfitLoss(**pl_data)
            self._apply_pnl_math(pl)
            out["pl"] = pl
        if "bs" in statement and isinstance(statement["bs"], dict):
            bs_data = {k: self._safe_float(statement["bs"].get(k), divisor=1.0) for k in BalanceSheet.__dataclass_fields__ if k in statement["bs"]}
            bs = BalanceSheet(**bs_data)
            self._apply_bs_math(bs)
            out["bs"] = bs
        if "cf" in statement and isinstance(statement["cf"], dict):
            cf_data = {k: self._safe_float(statement["cf"].get(k), divisor=1.0) for k in CashFlow.__dataclass_fields__ if k in statement["cf"]}
            cf = CashFlow(**cf_data)
            self._apply_cf_math(cf)
            out["cf"] = cf
        return out

    def _apply_pnl_math(self, pl: ProfitLoss):
        """Computes EBITDA, EBIT, Total Income."""
        if pl.revenue_from_operations is not None:
             pl.total_income = round((pl.revenue_from_operations or 0) + (pl.other_income or 0), 2)

        # Operating expenses may be available; compute quick operating profit if possible.
        if pl.total_income is not None and pl.operating_expenses is not None:
            # Not stored as a field; used indirectly for validation/insights.
            pass
        
        # EBITDA estimation if Profit Before Tax is known
        if pl.profit_before_tax is not None:
            # PBT + Interest + Depreciation = EBITDA
            pl.ebitda = round((pl.profit_before_tax or 0) + (pl.interest or 0) + (pl.depreciation or 0), 2)
            # EBITDA - Depreciation = EBIT
            pl.ebit = round((pl.ebitda or 0) - (pl.depreciation or 0), 2)

    def _apply_bs_math(self, bs: BalanceSheet):
        """Computes Total Equity, Debt, Working Capital."""
        if bs.equity_share_capital is not None or bs.reserves is not None:
            # Total Equity = Equity Share Capital + Other Equity + Non-Controlling Interest
            bs.total_equity = round((bs.equity_share_capital or 0) + (bs.reserves or 0) + (bs.non_controlling_interest or 0), 2)
        
        if bs.long_term_borrowings is not None or bs.short_term_borrowings is not None:
            bs.total_debt = round((bs.long_term_borrowings or 0) + (bs.short_term_borrowings or 0), 2)
            
        if bs.current_assets is not None and bs.current_liabilities is not None:
            bs.working_capital = round((bs.current_assets or 0) - (bs.current_liabilities or 0), 2)

    def _apply_cf_math(self, cf: CashFlow):
        """Computes Free Cash Flow."""
        if cf.cash_from_operations is not None and cf.capital_expenditure is not None:
            cf.free_cash_flow = round(cf.cash_from_operations - abs(cf.capital_expenditure), 2)

    def _safe_float(self, val: Any, divisor: float = 1.0) -> Optional[float]:
        if val is None or val == "" or str(val).lower() == "null":
            return None
        try:
            return round(float(str(val)) / divisor, 2) 
        except:
            return None

    def merge_financials(
        self,
        target: CompanyFinancials,
        source_data: Dict[str, Any],
        year: str,
        *,
        period_type: Literal["annual", "quarterly"] = "annual",
        source_name: str = "UNKNOWN",
        source_priority: Optional[int] = None,
        source_meta: Optional[Dict[str, Any]] = None,
    ):
        """
        Merge statement dataclasses using hierarchical precedence at field level.
        Provenance is recorded under target.metadata["provenance"].
        """
        prio = source_priority if source_priority is not None else self.DEFAULT_SOURCE_PRIORITY.get(source_name, 0)
        if "pl" in source_data and source_data["pl"] is not None:
            self._merge_dataclass(target, stmt="pl", period_type=period_type, year=year, source_obj=source_data["pl"], source_name=source_name, prio=prio, meta=source_meta)
        if "bs" in source_data and source_data["bs"] is not None:
            self._merge_dataclass(target, stmt="bs", period_type=period_type, year=year, source_obj=source_data["bs"], source_name=source_name, prio=prio, meta=source_meta)
        if "cf" in source_data and source_data["cf"] is not None:
            self._merge_dataclass(target, stmt="cf", period_type=period_type, year=year, source_obj=source_data["cf"], source_name=source_name, prio=prio, meta=source_meta)

    def _merge_dataclass(
        self,
        target: CompanyFinancials,
        *,
        stmt: Literal["pl", "bs", "cf"],
        period_type: Literal["annual", "quarterly"],
        year: str,
        source_obj: Any,
        source_name: str,
        prio: int,
        meta: Optional[Dict[str, Any]],
    ) -> None:
        if stmt == "pl":
            bucket = target.profit_loss[period_type]
        elif stmt == "bs":
            bucket = target.balance_sheet[period_type]
        else:
            bucket = target.cash_flow[period_type]

        if year not in bucket:
            bucket[year] = source_obj
            self._record_provenance(target, stmt=stmt, period_type=period_type, year=year, fields=self._fields_with_values(source_obj), source_name=source_name, prio=prio, meta=meta)
            return

        existing = bucket[year]
        for field, val in self._iter_fields(source_obj):
            if val is None:
                continue
            if self._should_override(target, stmt=stmt, period_type=period_type, year=year, field=field, new_prio=prio):
                setattr(existing, field, val)
                self._record_provenance(target, stmt=stmt, period_type=period_type, year=year, fields={field: val}, source_name=source_name, prio=prio, meta=meta)

    def _fields_with_values(self, obj: Any) -> Dict[str, Any]:
        return {k: v for k, v in self._iter_fields(obj) if v is not None}

    def _iter_fields(self, obj: Any):
        if is_dataclass(obj):
            for field in obj.__dataclass_fields__:
                yield field, getattr(obj, field)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                yield k, v
        else:
            for k in dir(obj):
                if k.startswith("_"):
                    continue
                try:
                    v = getattr(obj, k)
                except Exception:
                    continue
                if isinstance(v, (int, float)) or v is None:
                    yield k, v

    def _should_override(
        self,
        target: CompanyFinancials,
        *,
        stmt: str,
        period_type: str,
        year: str,
        field: str,
        new_prio: int,
    ) -> bool:
        prov = (((target.metadata.get("provenance") or {}).get(period_type) or {}).get(year) or {}).get(stmt) or {}
        prev = prov.get(field) or {}
        prev_prio = int(prev.get("priority", -1))
        if prev_prio < 0:
            # No provenance; be conservative and only overwrite if empty.
            return True
        return new_prio >= prev_prio

    def _record_provenance(
        self,
        target: CompanyFinancials,
        *,
        stmt: str,
        period_type: str,
        year: str,
        fields: Dict[str, Any],
        source_name: str,
        prio: int,
        meta: Optional[Dict[str, Any]],
    ) -> None:
        prov = target.metadata.setdefault("provenance", {})
        prov.setdefault(period_type, {})
        prov[period_type].setdefault(year, {})
        prov[period_type][year].setdefault(stmt, {})
        for field in fields.keys():
            prov[period_type][year][stmt][field] = {
                "source": source_name,
                "priority": prio,
                "meta": meta or {},
            }
