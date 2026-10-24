"""Strands model factory. Agents ask for a tier (strong / fast); the environment says which model that is, as a
LiteLLM model id. The provider is called directly (no proxy), so switching provider never touches agent code.

Environment:
  INSPECTOR_LLM_MODEL_STRONG  judgment-heavy calls (default ollama_chat/qwen3:14b)
  INSPECTOR_LLM_MODEL_FAST    cheap calls (default ollama_chat/qwen3:8b)
  INSPECTOR_LLM_API_BASE      endpoint override, e.g. an Ollama server (unset = the provider's default)
Examples of model ids: ollama_chat/qwen3:14b, anthropic/claude-sonnet-5-5, bedrock/<model or inference profile id>.
Credentials come from the provider's usual environment: ANTHROPIC_API_KEY, or the AWS role on AgentCore (Bedrock).
"""

from __future__ import annotations

import os
from typing import Any, Literal

Tier = Literal["strong", "fast"]
_DEFAULTS = {"strong": "ollama_chat/qwen3:14b", "fast": "ollama_chat/qwen3:8b"}


def model_id(tier: Tier) -> str:
    return os.environ.get(f"INSPECTOR_LLM_MODEL_{tier.upper()}", _DEFAULTS[tier])


def get_model(tier: Tier = "strong", **params: Any):
    """A Strands ``LiteLLMModel`` calling the configured provider directly."""
    from strands.models.litellm import LiteLLMModel

    client_args: dict[str, Any] = {"drop_params": True, "num_retries": 2, "timeout": 300}
    if os.environ.get("INSPECTOR_LLM_API_BASE"):
        client_args["api_base"] = os.environ["INSPECTOR_LLM_API_BASE"]
    return LiteLLMModel(client_args=client_args, model_id=model_id(tier),
                        params={"temperature": 0, "max_tokens": 2048, **params})
