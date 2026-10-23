"""CI guard: contracts must be valid and in sync with the pydantic source of truth."""

import json
import sys
from pathlib import Path

import yaml
from agentlens_agent.events import Event
from agentlens_evals.case import EvalCase
from openapi_spec_validator import validate
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "contracts"


def main() -> int:
    errors: list[str] = []
    validate(yaml.safe_load((C / "openapi.yaml").read_text()))
    for name, current in (
        ("agent_events.schema.json", TypeAdapter(Event).json_schema()),
        ("eval_case.schema.json", EvalCase.model_json_schema()),
    ):
        on_disk = json.loads((C / name).read_text())
        if on_disk != current:
            errors.append(f"{name} is stale: run `uv run python scripts/export_schemas.py`")
    if not list((ROOT / "gateway/migrations").glob("V*__*.sql")):
        errors.append("no SQL migrations found in gateway/migrations")
    for e in errors:
        print("ERROR:", e)
    print("contracts OK" if not errors else "contracts FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
