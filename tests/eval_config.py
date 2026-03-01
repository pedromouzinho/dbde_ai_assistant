"""
Configuração central do eval suite DBDE AI Assistant.
Não depende de config.py da app — define os seus próprios defaults.
"""

import os
from pathlib import Path

# Paths
EVAL_ROOT = Path(__file__).parent
DATASETS_DIR = EVAL_ROOT / "datasets"
RESULTS_DIR = EVAL_ROOT / "results"

# Thresholds de qualidade (Camada A - RAG)
RAG_FAITHFULNESS_THRESHOLD = 0.7
RAG_RELEVANCY_THRESHOLD = 0.7
RAG_CONTEXT_PRECISION_THRESHOLD = 0.6
RAG_CONTEXT_RECALL_THRESHOLD = 0.6

# Thresholds de qualidade (Camada B - Tools)
TOOL_SUCCESS_RATE_THRESHOLD = 0.9
TOOL_LATENCY_P95_THRESHOLD_MS = 5000

# Thresholds de qualidade (Camada C - Arena)
ARENA_WIN_RATE_THRESHOLD = 0.6

# Thresholds de qualidade (Camada D - User Stories)
US_STRUCTURE_THRESHOLD = 0.8
US_FORMATTING_THRESHOLD = 0.9
US_QUALITY_THRESHOLD = 0.7

# LLM Judge config (usa o mesmo Azure OpenAI do projecto)
# NOTE: "standard" (GPT-5) pode rejeitar max_tokens neste runtime; usar fast para compatibilidade imediata.
JUDGE_MODEL_TIER = "fast"
JUDGE_TEMPERATURE = 0.1
JUDGE_MAX_TOKENS = 500

# Eval run config
EVAL_TIMEOUT_PER_TEST_SECONDS = 60
EVAL_MAX_CONCURRENT_TESTS = 3

# Flags
DRY_RUN = os.getenv("EVAL_DRY_RUN", "false").lower() == "true"
MOCK_LLM = os.getenv("EVAL_MOCK_LLM", "true").lower() == "true"
VERBOSE = os.getenv("EVAL_VERBOSE", "false").lower() == "true"
