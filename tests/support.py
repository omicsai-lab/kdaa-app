"""Test-only transactional double. Never imported or selected by the application."""
import copy
import threading
from contextlib import contextmanager
from kdaa.domain import RunStatus

class MemorySession:
    def __init__(self, data, store):
        self.data, self.store = data, store
    def add_workspace(self, item): self.data["workspaces"][item.id] = item
    def get_workspace(self, id): return self.data["workspaces"].get(id)
    def list_workspaces(self): return list(self.data["workspaces"].values())
    def add_document(self, item): self.data["documents"][item.id] = item
    def get_document(self, id): return self.data["documents"].get(id)
    def list_documents(self, workspace_id):
        return sorted([d for d in self.data["documents"].values() if d.workspace_id == workspace_id], key=lambda d: d.created_at)
    def add_run(self, item): self.data["runs"][item.id] = item
    def update_run(self, item):
        if self.data["runs"][item.id].status not in {RunStatus.PENDING, RunStatus.RUNNING}:
            raise ValueError("Finalized run is immutable")
        self.data["runs"][item.id] = item
    def get_run(self, id): return self.data["runs"].get(id)
    def list_runs(self, workspace_id):
        return sorted([r for r in self.data["runs"].values() if r.workspace_id == workspace_id], key=lambda r: r.created_at, reverse=True)
    def unfinished_runs(self):
        return [r for r in self.data["runs"].values() if r.status in {RunStatus.PENDING, RunStatus.RUNNING}]
    def add_event(self, item):
        if item.action == self.store.fail_action:
            self.store.fail_action = None
            raise RuntimeError("Injected provenance write failure")
        self.data["events"][item.id] = item
    def list_events(self, workspace_id, run_id=None):
        return sorted([e for e in self.data["events"].values() if e.workspace_id == workspace_id and
                       (run_id is None or e.run_id == run_id)], key=lambda e: e.timestamp)

class MemoryStore:
    def __init__(self):
        self.data = {key: {} for key in ["workspaces", "documents", "runs", "events"]}
        self.fail_action = None
        self.lock = threading.RLock()
    @contextmanager
    def transaction(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            yield MemorySession(data, self)
            self.data = copy.deepcopy(data)
    def ready(self): return True
