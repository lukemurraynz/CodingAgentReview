import json
import os

p = os.path.join(os.environ["TEMP"], "f2.json")
d = json.load(open(p, encoding="utf8"))
seen = set()
for x in d:
    sm = (x or {}).get("properties", {}).get("statusMessage", {})
    err = sm.get("error", {}) if isinstance(sm, dict) else {}
    msg = err.get("message") or (sm.get("message") if isinstance(sm, dict) else "")
    res = (x or {}).get("properties", {}).get("targetResource", {}).get("resourceName", "?")
    key = str(msg)[:80]
    if key and key not in seen:
        seen.add(key)
        print(res, "::", str(msg)[:180])
