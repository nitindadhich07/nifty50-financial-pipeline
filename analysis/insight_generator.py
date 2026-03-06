import logging
from typing import Dict, List, Optional
from db.models import Filing, SessionLocal, Company

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class InsightGenerator:
    def __init__(self):
        pass

    def generate_growth_metrics(self, current_metrics: Dict, historical_metrics: Optional[Dict] = None) -> Dict:
        """
        Calculates YoY/QoQ growth.
        """
        insights = {}
        if not historical_metrics:
            return {"message": "No historical data available for comparison."}
        
        for metric in ["Revenue", "Net Profit", "EBITDA"]:
            current = current_metrics.get(metric, 0)
            previous = historical_metrics.get(metric, 0)
            
            if previous and previous != 0:
                growth = ((current - previous) / abs(previous)) * 100
                insights[f"{metric} Growth"] = f"{growth:.2f}%"
        
        return insights

    def generate_human_readable_summary(self, symbol: str, period: Dict, metrics: Dict, growth: Dict) -> str:
        """
        Generates a summary similar to the example in Part 11.
        """
        summary = f"Company: {symbol}\n"
        summary += f"Quarter: {period.get('quarter', 'N/A')} {period.get('financial_year', 'N/A')}\n\n"
        summary += "Key Insights:\n"
        
        for k, v in growth.items():
            if "Growth" in k:
                trend = "increased" if float(v.strip('%')) > 0 else "decreased"
                summary += f"- {k.split(' ')[0]} {trend} {v} YoY/QoQ.\n"
        
        if "Net Profit" in metrics and "Revenue" in metrics:
            margin = (metrics["Net Profit"] / metrics["Revenue"]) * 100
            summary += f"- Net margin is at {margin:.2f}%.\n"
            
        summary += "\nRisk signals:\n"
        # Heuristic risk signals
        if float(growth.get('Net Profit Growth', '0%').strip('%')) < -10:
            summary += "- Significant profit decline observed.\n"
        else:
            summary += "- No major risk signals detected based on current metrics.\n"
            
        return summary

if __name__ == "__main__":
    pass
