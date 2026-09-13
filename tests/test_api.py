from uuid import uuid4
import pytest
from .test_core import SOFTWARE, TEACHING, UNRELATED
from .test_parsing import docx_fixture, pdf_fixture

def workspace(client, name="API test"):
    r = client.post("/api/v1/workspaces", json={"name": name, "goal": "Python field notes"})
    assert r.status_code == 201, r.text
    return r.json()["id"]

def test_complete_http_workflow(client):
    id = workspace(client)
    r = client.post(f"/api/v1/workspaces/{id}/documents", files={"file": ("source.txt", SOFTWARE, "text/plain")})
    assert r.status_code == 201, r.text
    doc = r.json(); assert "raw_key" not in doc
    result = client.post(f"/api/v1/workspaces/{id}/runs", json={})
    assert result.status_code == 201, result.text
    run = result.json(); assert run["status"] == "succeeded"
    evidence = run["result"]["evidence"][0]
    view = client.get(f'/api/v1/runs/{run["id"]}/evidence/{evidence["id"]}')
    assert view.status_code == 200 and view.json()["quote"] == evidence["quote"]
    assert client.get(f'/api/v1/documents/{doc["id"]}/download').content == SOFTWARE
    assert client.get(f'/api/v1/documents/{doc["id"]}/content').json()["text"]
    history = client.get(f"/api/v1/workspaces/{id}/runs").json()
    assert history[0]["candidate_count"] == 1
    assert client.get(f'/api/v1/runs/{run["id"]}').json()["inputs"][0]["document_id"] == doc["id"]
    assert client.get(f'/api/v1/runs/{run["id"]}/export?format=json').json()["sources"][0]["normalized_text"]
    md = client.get(f'/api/v1/runs/{run["id"]}/export?format=markdown')
    assert md.status_code == 200 and "not a reproduction" in md.text
    events = client.get(f'/api/v1/runs/{run["id"]}/provenance').json()
    assert "document.imported" in [e["action"] for e in events]
    assert "run.succeeded" in [e["action"] for e in events]

@pytest.mark.parametrize("name,maker", [("source.pdf", pdf_fixture), ("source.docx", docx_fixture)])
def test_binary_upload_http(client, name, maker):
    id = workspace(client)
    r = client.post(f"/api/v1/workspaces/{id}/documents", files={"file": (name, maker())})
    assert r.status_code == 201, r.text
    assert client.post(f"/api/v1/workspaces/{id}/runs", json={}).json()["result"]["hypotheses"]

def test_demo_imports_through_normal_path(client):
    id = workspace(client)
    fixtures = client.get("/api/v1/demo").json()
    assert len(fixtures) == 3
    for fixture in fixtures:
        r = client.post(f"/api/v1/workspaces/{id}/documents", files={"file": (fixture["filename"], fixture["text"].encode())})
        assert r.status_code == 201
    run = client.post(f"/api/v1/workspaces/{id}/runs", json={}).json()
    assert len(run["inputs"]) == 3
    assert {h["candidate_type"] for h in run["result"]["hypotheses"]} == {"software", "dataset", "method", "teaching", "research_idea"}

@pytest.mark.parametrize("path", ["/api/v1/workspaces/", "/api/v1/runs/", "/api/v1/documents/"])
def test_unknown_uuid_errors(client, path):
    suffix = "/content" if "documents" in path else ""
    response = client.get(path + str(uuid4()) + suffix)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

def test_validation_and_empty_import(client):
    assert client.post("/api/v1/workspaces", json={"name": ""}).status_code == 422
    assert client.post("/api/v1/workspaces", json={"name": "   "}).status_code == 400
    id = workspace(client)
    assert client.post(f"/api/v1/workspaces/{id}/documents", files={"file": ("blank.txt", b"")}).status_code == 400
    assert client.post(f"/api/v1/workspaces/{id}/documents", files={"file": ("data.csv", b"x,y")}).status_code == 415
    assert client.post(f"/api/v1/workspaces/{id}/runs", json={}).status_code == 400
    assert client.get("/api/v1/runs/not-a-uuid").status_code == 422

def test_health_and_offline_docs(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json()["ready"]
    assert client.get("/api/v1/system").json()["live_llm"] is False
    docs = client.get("/docs")
    assert docs.status_code == 200 and '<script src="https:' not in docs.text
    schema = client.get("/openapi.json").json()
    assert "Run" in schema["components"]["schemas"]

def test_cross_site_requests_are_rejected(client):
    assert client.post("/api/v1/workspaces", json={"name": "attack"}, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert client.get("/api/v1/workspaces", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/health", headers={"Host": "untrusted.invalid"}).status_code == 400
    assert client.post("/api/v1/workspaces", json={"name": "safe"}, headers={"Origin": "http://localhost:5183"}).status_code == 201

# --- mode selection -----------------------------------------------------------------------

def test_system_reports_reference_only_by_default(client):
    info = client.get("/api/v1/system").json()
    assert info["mode"] == "reference" and info["modes"] == ["reference"]
    assert info["live_llm"] is False and info["local_only"] is True
    assert info["llm"]["enabled"] is False
    # Nothing about a model is advertised when live mode is not configured.
    assert info["llm"]["model"] == "" and info["llm"]["region"] == ""

def test_system_never_reports_a_credential(client):
    body = client.get("/api/v1/system").text.lower()
    for secret in ["aws_access_key", "aws_secret", "session_token", "credential", "profile"]:
        assert secret not in body

def test_requesting_an_unconfigured_mode_is_refused_not_downgraded(client):
    id = workspace(client)
    client.post(f"/api/v1/workspaces/{id}/documents", files={"file": ("source.txt", SOFTWARE, "text/plain")})
    r = client.post(f"/api/v1/workspaces/{id}/runs", json={"mode": "live"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "mode_unavailable"
    # No run was created and nothing silently ran in reference mode instead.
    assert client.get(f"/api/v1/workspaces/{id}/runs").json() == []

def test_unknown_mode_is_a_contract_error(client):
    id = workspace(client)
    r = client.post(f"/api/v1/workspaces/{id}/runs", json={"mode": "magic"})
    assert r.status_code == 422

def test_explicit_reference_mode_is_accepted(client):
    id = workspace(client)
    client.post(f"/api/v1/workspaces/{id}/documents", files={"file": ("source.txt", SOFTWARE, "text/plain")})
    run = client.post(f"/api/v1/workspaces/{id}/runs", json={"mode": "reference"}).json()
    assert run["status"] == "succeeded" and run["config"]["mode"] == "reference"
    assert run["engine_version"] == "reference-rules-v0.1"
