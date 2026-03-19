from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

@dataclass
class ProfitLoss:
    revenue_from_operations: Optional[float] = None
    other_income: Optional[float] = None
    total_income: Optional[float] = None
    operating_expenses: Optional[float] = None
    ebitda: Optional[float] = None
    ebit: Optional[float] = None
    interest: Optional[float] = None
    depreciation: Optional[float] = None
    profit_before_tax: Optional[float] = None
    tax: Optional[float] = None
    net_profit: Optional[float] = None
    eps: Optional[float] = None
    diluted_eps: Optional[float] = None
    exceptional_items: Optional[float] = None

@dataclass
class BalanceSheet:
    equity_share_capital: Optional[float] = None
    reserves: Optional[float] = None
    total_equity: Optional[float] = None
    total_debt: Optional[float] = None
    long_term_borrowings: Optional[float] = None
    short_term_borrowings: Optional[float] = None
    total_liabilities: Optional[float] = None
    total_assets: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    investments: Optional[float] = None
    receivables: Optional[float] = None
    inventory: Optional[float] = None
    ppe: Optional[float] = None
    intangible_assets: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    non_controlling_interest: Optional[float] = None
    deferred_tax_assets: Optional[float] = None
    deferred_tax_liabilities: Optional[float] = None
    working_capital: Optional[float] = None
    book_value: Optional[float] = None
    shares_outstanding: Optional[float] = None

@dataclass
class CashFlow:
    cash_from_operations: Optional[float] = None
    capital_expenditure: Optional[float] = None
    cash_from_investing: Optional[float] = None
    cash_from_financing: Optional[float] = None
    dividends_paid: Optional[float] = None
    net_cash_flow: Optional[float] = None
    free_cash_flow: Optional[float] = None

@dataclass
class FinancialPeriod:
    annual: Dict[str, Any] = field(default_factory=dict)
    quarterly: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CompanyFinancials:
    company_info: Dict[str, Any] = field(default_factory=dict)
    # Structure: { "annual": { "2025": ProfitLoss }, "quarterly": { "Q1-2025": ProfitLoss } }
    profit_loss: Dict[str, Dict[str, ProfitLoss]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    balance_sheet: Dict[str, Dict[str, BalanceSheet]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    cash_flow: Dict[str, Dict[str, CashFlow]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    standalone_profit_loss: Dict[str, Dict[str, ProfitLoss]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    standalone_balance_sheet: Dict[str, Dict[str, BalanceSheet]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    standalone_cash_flow: Dict[str, Dict[str, CashFlow]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    ratios: Dict[str, Dict[str, Dict[str, float]]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    growth: Dict[str, Dict[str, Dict[str, float]]] = field(default_factory=lambda: {"annual": {}, "quarterly": {}})
    metadata: Dict[str, Any] = field(default_factory=dict)
    insights: List[str] = field(default_factory=list)

def to_dict(obj):
    return asdict(obj)
