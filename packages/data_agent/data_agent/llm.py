"""Strands model factory. Agents only know two aliases (strong / fast); the LiteLLM proxy maps them to a
provider (Anthropic API, Ollama, Bedrock later), so switching provider never touches agent code.

Environment:
  INSPECTOR_LLM_BASE_URL      LiteLLM proxy (default http://localhost:4000; http://litellm:4000 in compose)
  INSPECTOR_LLM_API_KEY       proxy key (LITELLM_MASTER_KEY)
  INSPECTOR_LLM_MODEL_STRONG  alias for judgment-heavy calls (default inspector-strong)
  INSPECTOR_LLM_MODEL_FAST    alias for cheap calls (default inspector-fast)
"""

from __future__ import annotations

import os
from typing import Any, Literal

Tier = Literal["strong", "fast"]


def model_alias(tier: Tier) -> str:
    default = "inspector-strong" if tier == "strong" else "inspector-fast"
    return os.environ.get(f"INSPECTOR_LLM_MODEL_{tier.upper()}", default)


def get_model(tier: Tier = "strong", **params: Any):
    """A Strands ``LiteLLMModel`` talking to the LiteLLM proxy."""
    from strands.models.litellm import LiteLLMModel  # optional dependency: inspector-common[llm]

    return LiteLLMModel(
        client_args={
            "api_base": os.environ.get("INSPECTOR_LLM_BASE_URL", "http://localhost:4000"),
            "api_key": os.environ.get("INSPECTOR_LLM_API_KEY", "sk-inspector-local"),
        },
        model_id=f"litellm_proxy/{model_alias(tier)}",
        params={"temperature": 0, "max_tokens": 2048, **params},
    )
