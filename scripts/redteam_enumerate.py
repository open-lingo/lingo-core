#!/usr/bin/env python3
"""Red-team pass 1: deterministic route enumeration against a LOCAL lingo-core.

Reads /openapi.json, then hits every route with (a) no auth, (b) a garbage
Bearer, (c) an X-Dev-User impersonation header (DEBUG-bypass leak probe).
Records status, latency, body size. Classifies the public surface.

Local only. Server started with AWS creds blanked + DB_BACKEND=sqlite, so
nothing here can touch AWS or cost money.
"""
import json
import time
import sys
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8099"

# Dummy fillers for path params so a route actually routes instead of 404ing
# on a missing segment. Value chosen to be harmless + obviously a probe.
PROBE = "redteam-probe"
DUMMY_BODY = {"probe": PROBE, "name": PROBE, "content": PROBE, "text": PROBE}

VARIANTS = {
    "noauth": {},
    "garbage_bearer": {"Authorization": "Bearer not-a-real-jwt.aaa.bbb"},
    "devuser_header": {"X-Dev-User": "auth0|admin-probe"},  # DEBUG-bypass leak?
}


def fill(path: str) -> str:
    out = []
    for seg in path.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            out.append(PROBE)
        else:
            out.append(seg)
    return "/".join(out)


def main() -> None:
    spec = httpx.get(f"{BASE}/openapi.json", timeout=10).json()
    paths = spec.get("paths", {})
    rows = []
    with httpx.Client(timeout=15) as c:
        for raw_path, methods in sorted(paths.items()):
            url_path = fill(raw_path)
            for method, meta in methods.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                for vname, headers in VARIANTS.items():
                    body = DUMMY_BODY if method in ("post", "put", "patch") else None
                    t0 = time.perf_counter()
                    try:
                        r = c.request(
                            method.upper(),
                            f"{BASE}{url_path}",
                            headers=headers,
                            json=body,
                        )
                        ms = (time.perf_counter() - t0) * 1000
                        rows.append({
                            "path": raw_path,
                            "method": method.upper(),
                            "variant": vname,
                            "status": r.status_code,
                            "ms": round(ms, 1),
                            "bytes": len(r.content),
                        })
                    except Exception as e:  # noqa: BLE001
                        rows.append({
                            "path": raw_path, "method": method.upper(),
                            "variant": vname, "status": -1, "ms": -1,
                            "bytes": 0, "error": str(e)[:120],
                        })

    # Classify by the no-auth result.
    noauth = {(r["path"], r["method"]): r for r in rows if r["variant"] == "noauth"}
    public = [k for k, r in noauth.items() if 200 <= r["status"] < 300]
    server_err = [k for k, r in noauth.items() if r["status"] >= 500]
    protected = [k for k, r in noauth.items() if r["status"] in (401, 403)]

    # DEBUG-bypass leak: did the X-Dev-User variant get in where no-auth didn't?
    leaks = []
    for r in rows:
        if r["variant"] == "devuser_header" and 200 <= r["status"] < 300:
            na = noauth.get((r["path"], r["method"]))
            if na and not (200 <= na["status"] < 300):
                leaks.append((r["path"], r["method"]))

    print(f"total routes: {len(noauth)}")
    print(f"PUBLIC (2xx unauth): {len(public)}")
    print(f"PROTECTED (401/403): {len(protected)}")
    print(f"5xx on unauth: {len(server_err)}")
    print(f"DEBUG-BYPASS LEAKS (X-Dev-User got in): {len(leaks)}")
    print("\n--- PUBLIC SURFACE (attacker needs no account) ---")
    for path, method in sorted(public):
        r = noauth[(path, method)]
        print(f"  {method:6} {path:55} {r['status']}  {r['ms']:6}ms  {r['bytes']}B")
    if leaks:
        print("\n!!! DEBUG BYPASS LEAKS !!!")
        for path, method in leaks:
            print(f"  {method:6} {path}")
    if server_err:
        print("\n--- 5xx on unauth (fail-open? error-based info leak?) ---")
        for path, method in sorted(server_err):
            print(f"  {method:6} {path}  ({noauth[(path,method)]['status']})")

    with open("redteam_enum.json", "w") as f:
        json.dump({"rows": rows, "public": public, "protected": protected,
                   "server_err": server_err, "leaks": leaks}, f, indent=1)
    print("\nwrote redteam_enum.json")


if __name__ == "__main__":
    main()
