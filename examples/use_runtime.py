"""Run with --dry-run first; actual generation sends the sample prompt to the selected provider.

The connection is built in code, so no GenAI Bridge configuration file is read.
"""

import argparse
import json
from pathlib import Path

from tkn_genai_bridge import GenerationRequest, Profile, Runtime

parser = argparse.ArgumentParser()
parser.add_argument(
    "--provider", default="codex", choices=["codex", "claude-code", "github-copilot", "antigravity"]
)
parser.add_argument("--model", help="model name; omit to use the provider CLI's default")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()
here = Path(__file__).parent
request = GenerationRequest(
    prompt=(here / "prompt.txt").read_text(encoding="utf-8"),
    output_schema=json.loads((here / "output.schema.json").read_text(encoding="utf-8")),
)
profile = Profile(provider=args.provider, model=args.model)
with Runtime(profile, profile_name=f"example-{args.provider}") as runtime:
    result = runtime.plan(request) if args.dry_run else runtime.generate(request)
print(result.model_dump_json(indent=2))
