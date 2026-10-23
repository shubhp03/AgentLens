"""Export pydantic models to JSON Schema files under contracts/. Run: uv run python scripts/export_schemas.py"""

import json
from pathlib import Path

from agentlens_agent.events import Event
from agentlens_evals.case import EvalCase
from pydantic import TypeAdapter

OUT = Path(__file__).resolve().parents[1] / "contracts"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "agent_events.schema.json").write_text(
        json.dumps(TypeAdapter(Event).json_schema(), indent=2) + "\n"
    )
    (OUT / "eval_case.schema.json").write_text(
        json.dumps(EvalCase.model_json_schema(), indent=2) + "\n"
    )
    print("wrote contracts/agent_events.schema.json, contracts/eval_case.schema.json")


if __name__ == "__main__":
    main()
