import unittest

from pipeline_v3.parsers.xbrl_parser import MCAXBRLInstanceParser


class TestXBRLParser(unittest.TestCase):
    def test_context_and_unit_conversion(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
            xmlns:mca="http://www.mca.gov.in/xbrit/2016-12-01/mca-ind-as">
  <xbrli:context id="C_FY2025_CONS">
    <xbrli:entity><xbrli:identifier scheme="test">X</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2024-04-01</xbrli:startDate>
      <xbrli:endDate>2025-03-31</xbrli:endDate>
    </xbrli:period>
    <xbrli:segment>Consolidated</xbrli:segment>
  </xbrli:context>

  <xbrli:context id="I_FY2025_CONS">
    <xbrli:entity><xbrli:identifier scheme="test">X</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:instant>2025-03-31</xbrli:instant>
    </xbrli:period>
    <xbrli:segment>Consolidated</xbrli:segment>
  </xbrli:context>

  <xbrli:unit id="U_INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>

  <mca:RevenueFromOperations contextRef="C_FY2025_CONS" unitRef="U_INR">10000000000</mca:RevenueFromOperations>
  <mca:ProfitLossForPeriod contextRef="C_FY2025_CONS" unitRef="U_INR">500000000</mca:ProfitLossForPeriod>
  <mca:Assets contextRef="I_FY2025_CONS" unitRef="U_INR">25000000000</mca:Assets>
</xbrli:xbrl>
"""
        p = MCAXBRLInstanceParser(target_unit="INR_CRORE", prefer_consolidated=True)
        parsed = p.parse_bytes(xml)
        stmts = parsed["statements"]

        self.assertIn("FY2025", stmts["pl"])
        self.assertAlmostEqual(stmts["pl"]["FY2025"]["revenue_from_operations"], 1000.0, places=2)
        self.assertAlmostEqual(stmts["pl"]["FY2025"]["net_profit"], 50.0, places=2)
        self.assertAlmostEqual(stmts["bs"]["FY2025"]["total_assets"], 2500.0, places=2)


if __name__ == "__main__":
    unittest.main()

