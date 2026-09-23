"""Run with --dry-run first; actual generation sends the sample prompt to the selected provider."""

import argparse
import json
from pathlib import Path

from tkn_genai_bridge import GenerationRequest, Runtime, load_profile

parser = argparse.ArgumentParser()
parser.add_argument("--profile", default="codex-default")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
here = Path(__file__).parent
request = GenerationRequest(
    prompt=(here / "prompt.txt").read_text(encoding="utf-8"),
    output_schema=json.loads((here / "output.schema.json").read_text(encoding="utf-8")),
)
runtime = Runtime(load_profile(args.profile))
result = runtime.plan(request) if args.dry_run else runtime.generate(request)
print(result.model_dump_json(indent=2))
