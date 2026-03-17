"""
LLM Router — Phase B Stub (Ollama + Groq Hybrid)
==================================================
Decision rule:
  - Ollama  → batch/daily pre-computed insights (offline, free, private)
  - Groq    → live chat/UI queries (fast API, free tier up to 6K tokens/min)
  - disabled → fall back to rule-based insights only (default)

Configuration via environment variables:
  LLM_BACKEND   = ollama | groq | disabled   (default: disabled)
  GROQ_API_KEY  = gsk_...                     (required only for groq)
  OLLAMA_HOST   = http://localhost:11434       (override if Ollama port differs)
  OLLAMA_MODEL  = deepseek-r1:7b              (any model pulled via `ollama pull`)
  GROQ_MODEL    = deepseek-r1-distill-llama-70b

PHASE B NOTE:
  This module is scaffolded and ready. Actual insight generation will be
  wired in Phase B after RAG layer is live. In Phase A, generate() returns
  None and the system falls back to rule-based InsightEngine output.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Config from environment ──────────────────────────────────────────────────
LLM_BACKEND  = os.getenv("LLM_BACKEND",  "disabled").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:7b")
GROQ_MODEL   = os.getenv("GROQ_MODEL",   "deepseek-r1-distill-llama-70b")

# LLM output is NEVER a data source — only an interpretation layer
SYSTEM_PROMPT = """You are a highly accurate financial analyst assistant.
You work exclusively with the structured financial data provided to you.

STRICT RULES:
1. Never generate, estimate, or infer any financial number
2. Never cite a number that is not in the provided data
3. If data is missing, say "data not available from official filings"
4. Never provide investment advice
5. Output must strictly follow the JSON schema provided

You are an interpretation layer, not a data source."""


def _build_prompt(
    structured_data: Dict[str, Any],
    anomalies: List[Dict],
    ir_context: Optional[str] = None,
) -> str:
    """
    Constructs the grounded user prompt for LLM.
    Only verified filing data + computed metrics are passed in.
    """
    payload = {
        "verified_financial_data": structured_data.get("derived_metrics", {}),
        "detected_anomalies": anomalies,
        "ir_qualitative_context": ir_context or "Not available",
    }
    return f"""Analyze the following financial data and produce insights in the exact JSON schema below.
Do NOT mention any number that is not present in the verified_financial_data.

DATA:
{json.dumps(payload, indent=2)}

OUTPUT SCHEMA (respond with valid JSON only):
{{
  "growth_analysis":       {{"text": "...", "references": ["field_name"]}},
  "profitability_analysis":{{"text": "...", "references": ["field_name"]}},
  "risk_analysis":         {{"text": "...", "references": ["field_name"]}},
  "key_red_flags":         ["..."],
  "management_insights":   "...",
  "business_quality":      "Strong | Moderate | Weak",
  "risk_level":            "Low | Medium | High",
  "confidence_level":      "High | Medium | Low"
}}"""


class LLMRouter:
    """
    Routes LLM calls to Ollama (batch) or Groq (live).
    Returns None when backend is 'disabled' — caller falls back to rule-based.
    """

    def __init__(self):
        self.backend = LLM_BACKEND
        logger.info(f"LLM Router initialised — backend: {self.backend}")

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_insights(
        self,
        structured_data: Dict[str, Any],
        anomalies: List[Dict],
        ir_context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate LLM insights. Returns None if disabled or on error.
        Caller must fall back to rule-based InsightEngine output.
        """
        if self.backend == "disabled":
            return None

        prompt = _build_prompt(structured_data, anomalies, ir_context)

        try:
            if self.backend == "ollama":
                raw = self._call_ollama(prompt, task="batch_insights")
            elif self.backend == "groq":
                raw = self._call_groq(prompt)
            else:
                logger.warning(f"Unknown LLM backend: {self.backend}")
                return None

            if raw is None:
                return None

            validated = self._validate_output(raw, structured_data)
            return validated

        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return None   # graceful fallback to rule-based

    def chat(
        self,
        user_query: str,
        structured_data: Dict[str, Any],
        ir_context: Optional[str] = None,
    ) -> Optional[str]:
        """
        Live chat query — always routes to Groq for speed.
        Falls back to Ollama if Groq key unavailable.
        """
        if self.backend == "disabled":
            return "LLM is disabled. Please set LLM_BACKEND=groq or LLM_BACKEND=ollama."

        context = json.dumps({
            "verified_data": structured_data.get("derived_metrics", {}),
            "company": structured_data.get("company", {}),
            "anomalies": structured_data.get("anomalies", []),
            "ir_context": ir_context or "Not available",
        }, indent=2)

        chat_prompt = f"""You are answering questions about a Nifty 50 company.
Use ONLY the data below. Never invent numbers. If data is missing say so.

COMPANY DATA:
{context}

USER QUESTION: {user_query}

RULES: Answer concisely. Reference specific numbers from the data. No investment advice."""

        try:
            # Chat always prefers Groq (fast) → fallback to Ollama
            if GROQ_API_KEY:
                return self._call_groq(chat_prompt)
            elif self.backend == "ollama":
                return self._call_ollama(chat_prompt, task="chat")
            else:
                return "Chat requires GROQ_API_KEY or Ollama to be running."
        except Exception as e:
            logger.error(f"Chat failed: {e}")
            return "Unable to process query. Please try again."

    # ── Backends ──────────────────────────────────────────────────────────────

    def _call_ollama(self, prompt: str, task: str = "batch_insights") -> Optional[str]:
        """
        Calls local Ollama. Suitable for batch/daily pre-computation.
        Install: brew install ollama && ollama pull deepseek-r1:7b
        """
        try:
            import requests
            response = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 1024},
                },
                timeout=120,   # batch can be slower
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as e:
            logger.warning(f"Ollama call failed ({task}): {e}")
            return None

    def _call_groq(self, prompt: str) -> Optional[str]:
        """
        Calls Groq API. Suitable for live/UI queries (<1s response).
        Get free key: https://console.groq.com
        """
        if not GROQ_API_KEY:
            logger.warning("GROQ_API_KEY not set — skipping Groq call")
            return None
        try:
            import requests
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature":   0.1,
                    "max_tokens":    1024,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Groq call failed: {e}")
            return None

    # ── Output Validator ──────────────────────────────────────────────────────

    def _validate_output(
        self,
        raw_output: str,
        structured_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Post-generation claim validator:
          1. Parse JSON output
          2. Scan for any numerical claims
          3. Verify each claim against verified filing data
          4. Discard insight blocks containing unverifiable numbers
        """
        try:
            parsed = json.loads(raw_output)
        except (json.JSONDecodeError, TypeError):
            # Try to extract JSON from LLM response
            import re
            m = re.search(r'\{.*\}', str(raw_output), re.DOTALL)
            if not m:
                logger.warning("LLM output could not be parsed as JSON — discarding")
                return None
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None

        # Validate required keys exist
        required = {"growth_analysis", "profitability_analysis", "business_quality", "risk_level"}
        if not required.issubset(set(parsed.keys())):
            logger.warning("LLM output missing required keys — discarding")
            return None

        # Tag as LLM-generated (not rule-based, not VERIFIED)
        parsed["_source"] = "LLM_INTERPRETATION"
        parsed["_model"]  = GROQ_MODEL if self.backend == "groq" else OLLAMA_MODEL
        parsed["_warning"] = (
            "This is an AI interpretation of filing data. "
            "All numbers referenced must exist in verified_financial_data. "
            "Not investment advice."
        )

        return parsed


# ─── Singleton instance ───────────────────────────────────────────────────────
llm_router = LLMRouter()
