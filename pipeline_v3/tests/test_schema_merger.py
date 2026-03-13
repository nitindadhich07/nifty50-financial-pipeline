import unittest

from pipeline_v3.transformers.financial_mapper import CompanyFinancials, ProfitLoss
from pipeline_v3.transformers.schema_normalizer import SchemaNormalizer


class TestSchemaMerger(unittest.TestCase):
    def test_field_level_precedence(self):
        norm = SchemaNormalizer()
        fin = CompanyFinancials()

        # Low priority source first
        norm.merge_financials(
            fin,
            {"pl": ProfitLoss(revenue_from_operations=10.0)},
            "FY2025",
            period_type="annual",
            source_name="PDF",
        )
        self.assertEqual(fin.profit_loss["annual"]["FY2025"].revenue_from_operations, 10.0)

        # Higher priority source overrides
        norm.merge_financials(
            fin,
            {"pl": ProfitLoss(revenue_from_operations=12.0)},
            "FY2025",
            period_type="annual",
            source_name="MCA_XBRL",
        )
        self.assertEqual(fin.profit_loss["annual"]["FY2025"].revenue_from_operations, 12.0)

        prov = fin.metadata["provenance"]["annual"]["FY2025"]["pl"]["revenue_from_operations"]
        self.assertEqual(prov["source"], "MCA_XBRL")


if __name__ == "__main__":
    unittest.main()

