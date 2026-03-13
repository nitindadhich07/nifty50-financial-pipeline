import unittest

from pipeline_v3.validation.financial_validator import FinancialValidator


class TestValidator(unittest.TestCase):
    def test_flags_balance_sheet_mismatch(self):
        v = FinancialValidator()
        fin = {
            "profit_loss": {"annual": {"FY2025": {"revenue_from_operations": 100.0, "net_profit": 10.0}}},
            "balance_sheet": {"annual": {"FY2025": {"total_assets": 100.0, "total_equity": 10.0, "total_liabilities": 50.0}}},
            "cash_flow": {"annual": {}},
        }
        anomalies = v.validate(fin)
        self.assertTrue(any("BS Mismatch" in a for a in anomalies))


if __name__ == "__main__":
    unittest.main()

