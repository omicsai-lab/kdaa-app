"""Real HTTP acceptance test; standard library only, no paid calls or destructive cleanup.

Example: python scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
The test creates one clearly named workspace. --restart restarts only this Compose backend.
"""
import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from uuid import uuid4


def request(base, path, body=None, method=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
        headers=headers or ({"Content-Type": "application/json"} if data is not None else {}))
    with urllib.request.urlopen(req, timeout=120) as response:
        data = response.read()
        return json.loads(data) if "json" in response.headers.get("Content-Type", "") else data

def wait_ready(base):
    for _ in range(60):
        try:
            if request(base, "/ready")["ready"]: return
        except (OSError, ValueError): pass
        time.sleep(1)
    raise RuntimeError("API did not become ready within 60 seconds.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8183")
    parser.add_argument("--restart", action="store_true", help="Non-destructively restart this Compose backend, then verify persisted content.")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    wait_ready(base)
    workspace = request(base, "/api/v1/workspaces", {"name": f"Acceptance {uuid4().hex[:6]}", "goal": "Reuse teaching and software assets"})
    fixtures = request(base, "/api/v1/demo")
    ids = []
    for sample in fixtures:
        boundary = f"kdaa-{uuid4().hex}"
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{sample["filename"]}"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
                + sample["text"].encode() + f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(base + f'/api/v1/workspaces/{workspace["id"]}/documents', data=data,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as response: ids.append(json.load(response)["id"])
    run = request(base, f'/api/v1/workspaces/{workspace["id"]}/runs', {"document_ids": ids})
    assert run["status"] == "succeeded", run.get("error")
    assert len(run["result"]["hypotheses"]) >= 3
    evidence = run["result"]["evidence"][0]
    inspected = request(base, f'/api/v1/runs/{run["id"]}/evidence/{evidence["id"]}')
    assert inspected["verified"] and inspected["quote"] == evidence["quote"]
    exported = request(base, f'/api/v1/runs/{run["id"]}/export?format=json')
    assert len(exported["sources"]) == len(ids)
    for item in run["result"]["evidence"]:
        source = next(s for s in exported["sources"] if s["id"] == item["document_id"])
        assert source["normalized_text"][item["start"]:item["end"]] == item["quote"]
    assert b"KDAA run report" in request(base, f'/api/v1/runs/{run["id"]}/export?format=markdown')
    if args.restart:
        subprocess.run(["docker", "compose", "restart", "backend"], check=True)
        wait_ready(base)
        persisted = request(base, f'/api/v1/runs/{run["id"]}')
        assert persisted == run, "Saved run changed across backend restart"
        assert request(base, f'/api/v1/documents/{ids[0]}/content')["text"]
    print(json.dumps({"passed": True, "workspace_id": workspace["id"], "run_id": run["id"],
        "documents": len(ids), "candidates": len(run["result"]["hypotheses"]),
        "restart_persistence_checked": args.restart}, indent=2))

if __name__ == "__main__":
    main()
