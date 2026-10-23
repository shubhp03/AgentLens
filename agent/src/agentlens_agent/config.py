from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class AgentConfig(BaseModel):
    """Everything that defines an agent variant. The gateway snapshots this immutably."""

    name: str
    provider: str
    model: str
    system_prompt_id: str = "system_v1"
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_steps: int = Field(default=12, ge=1, le=50)
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_output_tokens: int = Field(default=2048, ge=64)
    tool_allowlist: list[str] = Field(default_factory=list)
    params: dict[str, object] = Field(default_factory=dict)


def load_agent_config(path: str | Path) -> AgentConfig:
    return AgentConfig.model_validate(yaml.safe_load(Path(path).read_text()))


def load_system_prompt(prompt_id: str, prompts_dir: Path = PROMPTS_DIR) -> tuple[str, str]:
    """Return (text, sha256). The sha is stored with every config so prompt drift is visible."""
    text = (prompts_dir / f"{prompt_id}.md").read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()
