"""Strands model factory. Agents ask for a tier (strong / fast); the environment says which model that is and
where it runs, so switching provider never touches agent code.

Environment:
  INSPECTOR_LLM_MODEL_STRONG  judgment-heavy calls, e.g. Qwen/Qwen3-32B-AWQ or a Bedrock model / inference profile id
  INSPECTOR_LLM_MODEL_FAST    cheap calls (defaults to the strong model)
  INSPECTOR_LLM_API_BASE      an OpenAI-compatible server, e.g. the RunPod vLLM pod (…/v1). Unset: Amazon Bedrock,
                              with the usual AWS credentials (the runtime's role) and AWS_REGION.
  INSPECTOR_LLM_API_KEY       key for INSPECTOR_LLM_API_BASE
A caller can also pass any Strands model object directly (e.g. run_validation(..., model=...)).
"""

from __future__ import annotations

import os
from typing import Any, Literal

Tier = Literal["strong", "fast"]
MAX_TOKENS = 2048


def model_id(tier: Tier) -> str:
    value = os.environ.get(f"INSPECTOR_LLM_MODEL_{tier.upper()}") or os.environ.get("INSPECTOR_LLM_MODEL_STRONG")
    if not value:
        raise RuntimeError("set INSPECTOR_LLM_MODEL_STRONG (and INSPECTOR_LLM_API_BASE for an OpenAI-compatible server)")
    return value


def get_model(tier: Tier = "strong", **params: Any):
    """An OpenAI-compatible server when INSPECTOR_LLM_API_BASE is set, otherwise Bedrock."""
    if base := os.environ.get("INSPECTOR_LLM_API_BASE"):
        try:
            from strands.models.openai import OpenAIModel
        except ImportError as exc:
            raise RuntimeError("INSPECTOR_LLM_API_BASE is set: install the package's `runpod` extra (openai)") from exc

        return OpenAIModel(client_args={"base_url": base, "api_key": os.environ.get("INSPECTOR_LLM_API_KEY") or "none",
                                        "timeout": 300, "max_retries": 2},
                           model_id=model_id(tier), params={"temperature": 0, "max_tokens": MAX_TOKENS, **params})
    from strands.models.bedrock import BedrockModel

    return BedrockModel(model_id=model_id(tier), region_name=os.environ.get("AWS_REGION") or None,
                        temperature=0, max_tokens=MAX_TOKENS, **params)
