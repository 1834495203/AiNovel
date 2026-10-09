"""Export the FastAPI schema without reading local environment secrets."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ainovel.controller.api import create_app
from ainovel.core.config import Settings


schema = create_app(Settings(_env_file=None, llm_provider="mock")).openapi()
target = Path("src/ainovel/frontend/openapi.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
