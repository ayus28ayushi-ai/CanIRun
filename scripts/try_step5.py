# scripts/try_step5.py  (run: python -m scripts.try_step5)
import json
from repoready.github_fetcher import fetch_repository, cleanup
from repoready.tools.inspect_repository import inspect_repository
from repoready.tools.project_config import inspect_project_config

import sys
ctx = fetch_repository(sys.argv[1])
# URL = "https://github.com/ayus28ayushi-ai/CanIRun-demo-broken"

# ctx = fetch_repository(URL)
try:
    # Run inspect_repository first so key_files are available
    inspect_repository(ctx)

    print("== inspect_project_config ==")
    result = inspect_project_config(ctx)

    # Pretty-print, but truncate any long lists for readability
    print(json.dumps(result, indent=2, default=str))

    print("\nActivity log:", ctx.activity)
    print("tool_calls  :", ctx.tool_calls)
finally:
    cleanup(ctx)
    print("Cleaned up.")
