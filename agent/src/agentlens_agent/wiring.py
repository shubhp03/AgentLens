"""Builds an AgentRunner from a request. Kept separate so main.py stays thin and testable."""

from __future__ import annotations

import json
import os
from uuid import UUID

from agentlens_agent.config import AgentConfig, load_system_prompt
from agentlens_agent.cost import PricingTable
from agentlens_agent.llm.anthropic_client import AnthropicClient
from agentlens_agent.llm.base import LLMClient
from agentlens_agent.loop import AgentRunner
from agentlens_agent.mcp_registry import McpToolRegistry


def build_llm(config: AgentConfig) -> LLMClient:
    if config.provider == "anthropic":
        return AnthropicClient(model=config.model)
    # AG-04 (P1): add an OpenAI adapter implementing LLMClient.
    raise ValueError(f"unsupported provider: {config.provider}")


def build_runner(config: AgentConfig, run_id: UUID, registry: McpToolRegistry) -> AgentRunner:
    prompt, _sha = load_system_prompt(config.system_prompt_id)
    pricing = PricingTable.from_yaml(os.environ.get("PRICING_FILE", "config/pricing.yaml"))
    return AgentRunner(
        llm=build_llm(config),
        tools=registry,
        config=config,
        system_prompt=prompt,
        pricing=pricing,
        run_id=run_id,
    )


def mcp_servers_from_env() -> dict[str, str]:
    return json.loads(os.environ.get("MCP_SERVERS", "{}"))
