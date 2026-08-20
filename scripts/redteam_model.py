#!/usr/bin/env python3
"""Local red-team analyst: qwen3-coder-next reads the enum + router source and
returns a ranked attack plan + un-mount safety calls. Structured output."""
import json, pathlib, sys
import httpx

scratch = pathlib.Path(__file__).parent
bundle = json.loads((scratch / "redteam_bundle.json").read_text())
model = sys.argv[1] if len(sys.argv) > 1 else "qwen3-coder-next:q4_K_M"

SCHEMA = {
  "type": "object",
  "properties": {
    "attacks": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "target_route": {"type": "string"},
          "needs_account": {"type": "boolean"},
          "mechanism": {"type": "string"},
          "impact": {"type": "string", "enum": ["cost", "availability", "data", "integrity", "info-leak"]},
          "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
          "concrete_request": {"type": "string"},
        },
        "required": ["name", "target_route", "needs_account", "mechanism", "impact", "severity"],
      },
    },
    "unmount_safe": {"type": "array", "items": {"type": "string"},
      "description": "router prefixes safe to un-mount for a landing+signin+learn+practice beta"},
    "keep_mounted": {"type": "array", "items": {"type": "string"}},
    "notes": {"type": "string"},
  },
  "required": ["attacks", "unmount_safe", "keep_mounted"],
}

prompt = f"""You are a red-team security analyst for a FastAPI language-learning
backend deployed as an AWS Lambda behind a PUBLIC Function URL (authorization_type=NONE,
no API gateway, no WAF, no rate limiting). Auth is Auth0 JWT via a FastAPI dependency.
The owner wants a BETA exposing only four user surfaces: landing page, sign in,
LEARN (bundled lessons, no server content fetch), and PRACTICE (SRS review).
Core-loop routers the app needs: boot, users, srs, progress. Everything else
(community, social, decks, stories, quests, ads, tags, admin, platform_settings,
finance) is a candidate to un-mount.

A deterministic scan already found: {len(bundle['enum_summary']['public'])} routes
are reachable WITH NO AUTH. Public routes: {json.dumps(bundle['enum_summary']['public'])}.
Routes 500ing on unauth (fail-closed internal-token routes): {json.dumps(bundle['enum_summary']['server_err'])}.
DEBUG-bypass leaks: {json.dumps(bundle['enum_summary']['leaks'])} (none — good).

Key facts: the public community reads (list_threads, list_categories, list_tags)
run DynamoDB _paginate_scan (full-table scan + post-filter), so request cost grows
with table size and needs no account. No rate limiting exists anywhere.

SOURCE (truncated):
--- main.py middleware ---
{bundle['main_middleware']}
--- auth dependencies ---
{bundle['auth_dependencies']}
--- community router ---
{bundle['community_router']}
--- community db scans ---
{bundle['community_db_scans']}

Produce a RANKED attack plan (most severe first) focused on what an attacker can
do to run up AWS cost, break availability (Lambda concurrency starvation), leak
info, or corrupt integrity — WITHOUT a valid account where possible. Then state
which router prefixes are SAFE to un-mount for the four-surface beta and which
MUST stay. Be concrete and specific to THIS code; do not invent routes."""

body = {
  "model": model, "stream": False, "think": False, "format": SCHEMA,
  "messages": [{"role": "user", "content": prompt}],
  "options": {"num_ctx": 16384, "num_predict": 4096, "temperature": 0},
}
print(f"calling {model} ...", flush=True)
r = httpx.post("http://localhost:11434/api/chat", json=body, timeout=1200)
r.raise_for_status()
txt = r.json()["message"]["content"]
try:
    out = json.loads(txt)
except Exception:
    print("UNPARSEABLE (MLX format trap?):", txt[:400]); sys.exit(1)
(scratch / "redteam_model_out.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
