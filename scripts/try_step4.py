# scripts/try_step4.py  (run: python -m scripts.try_step4)
import json
from repoready.github_fetcher import fetch_repository, cleanup
from repoready.tools.inspect_repository import inspect_repository
from repoready.tools.read_file import read_file
 
URL = "https://github.com/ayus28ayushi-ai/CanIRun-demo-broken"   # <- edit this
info = fetch_repository(URL)
    


ctx = fetch_repository(URL)
try:
    print("== inspect_repository ==")
    print(json.dumps(inspect_repository(ctx), indent=2))

    for path in ["../../etc/passwd", "nope.txt","README.md" ,"package.json"]:
        print(f"\n== read_file({path!r}) ==")
        result = read_file(ctx, path)
        # keep the output short
        if result.get("content"):
            result["content"] = result["content"][:200] + "..."
        print(json.dumps(result, indent=2))
finally:
    print("\nActivity log:", ctx.activity)
    cleanup(ctx)
    # call your cleanup helper here, e.g. cleanup(ctx)