"""
MCA (AOC-4) XBRL tag mapping.

Keep this as data-only so it can be extended without touching parser logic.
We match using QName localname primarily because MCA namespaces vary by taxonomy version.
"""

# Canonical schema fields -> candidate XBRL element localnames (Ind-AS / IGAAP variants).
# Not exhaustive; add as you encounter new tags.
MCA_LOCALNAME_MAP = {
    "pl": {
        "revenue_from_operations": [
            "RevenueFromOperations",
            "RevenueFromOperationsNet",
            "RevenueFromOperationsGross",
            "Revenue",
            "RevenueFromContractWithCustomer",
        ],
        "other_income": [
            "OtherIncome",
        ],
        "total_income": [
            "TotalIncome",
            "Income",
        ],
        "operating_expenses": [
            "TotalExpenses",
            "Expenses",
            "EmployeeBenefitExpense",
            "CostOfMaterialsConsumed",
            "PurchasesOfStockInTrade",
            "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
            "OtherExpenses",
        ],
        "interest": [
            "FinanceCosts",
            "FinanceCost",
            "InterestExpense",
            "Interest",
        ],
        "depreciation": [
            "DepreciationAndAmortisationExpense",
            "Depreciation",
            "Amortisation",
        ],
        "profit_before_tax": [
            "ProfitBeforeTax",
            "ProfitLossBeforeTax",
        ],
        "tax": [
            "TaxExpense",
            "CurrentTaxExpense",
            "IncomeTaxExpense",
        ],
        "net_profit": [
            "ProfitLossForPeriod",
            "ProfitLoss",
            "ProfitLossForTheYear",
            "ProfitLossAttributableToOwnersOfParent",
        ],
        "eps": [
            "BasicEarningsLossPerShareFromContinuingOperations",
            "BasicEarningsLossPerShare",
            "EarningsPerShareBasic",
            "BasicEarningsPerShare",
        ],
    },
    "bs": {
        "equity_share_capital": [
            "EquityShareCapital",
            "ShareCapital",
        ],
        "reserves": [
            "OtherEquity",
            "ReservesAndSurplus",
            "Reserves",
        ],
        "total_equity": [
            "Equity",
            "TotalEquity",
            "TotalEquityAttributableToOwnersOfParent",
        ],
        "long_term_borrowings": [
            "BorrowingsNonCurrent",
            "NonCurrentBorrowings",
            "LongTermBorrowings",
        ],
        "short_term_borrowings": [
            "BorrowingsCurrent",
            "CurrentBorrowings",
            "ShortTermBorrowings",
        ],
        "total_debt": [
            "Borrowings",
            "TotalBorrowings",
        ],
        "total_liabilities": [
            "Liabilities",
            "TotalLiabilities",
            "TotalEquityAndLiabilities",
        ],
        "total_assets": [
            "Assets",
            "TotalAssets",
        ],
        "cash_and_equivalents": [
            "CashAndCashEquivalents",
            "CashAndCashEquivalentsAtCarryingValue",
        ],
        "investments": [
            "Investments",
            "FinancialAssets",
        ],
        "receivables": [
            "TradeReceivables",
            "Receivables",
        ],
        "inventory": [
            "Inventories",
            "Inventory",
        ],
        "ppe": [
            "PropertyPlantAndEquipment",
        ],
        "intangible_assets": [
            "IntangibleAssets",
            "IntangibleAssetsOtherThanGoodwill",
        ],
    },
    "cf": {
        "cash_from_operations": [
            "NetCashFlowsFromUsedInOperatingActivities",
            "NetCashFromOperatingActivities",
            "CashFlowsFromUsedInOperatingActivities",
        ],
        "capital_expenditure": [
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PurchaseOfFixedAssets",
            "CapitalExpenditure",
        ],
        "cash_from_investing": [
            "NetCashFlowsFromUsedInInvestingActivities",
            "NetCashFromInvestingActivities",
        ],
        "cash_from_financing": [
            "NetCashFlowsFromUsedInFinancingActivities",
            "NetCashFromFinancingActivities",
        ],
        "dividends_paid": [
            "DividendsPaidClassifiedAsFinancingActivities",
            "DividendsPaid",
        ],
        "net_cash_flow": [
            "NetIncreaseDecreaseInCashAndCashEquivalents",
            "NetCashFlow",
        ],
    },
}

