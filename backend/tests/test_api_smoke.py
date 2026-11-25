from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.client import close_db, init_db
from app.main import app
from app.services.artifact_service import create_artifact


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    base_tmp = Path(__file__).resolve().parent.parent / ".pytest_tmp"
    base_tmp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base_tmp) as td:
        data_dir = Path(td) / ".agenthub-data"
        monkeypatch.setenv("AGENTHUB_DATA_DIR", str(data_dir))
        close_db()
        init_db()
        with TestClient(app) as c:
            yield c
        close_db()


def test_health_platform_settings(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    plat = client.get("/api/platform").json()
    assert plat["platform"] in ("windows", "darwin", "linux")

    s0 = client.get("/api/settings").json()["settings"]
    assert s0["id"] == "singleton"
    assert s0["companionMode"] == "off"

    s1 = client.patch("/api/settings", json={"deepseekApiKey": "sk-test-123"}).json()["settings"]
    assert s1["deepseekApiKey"] == "sk-test-123"


def test_agents_crud(client: TestClient):
    res = client.get("/api/agents").json()
    assert len(res["agents"]) == 5
    assert any(a["id"] == "ag_orchestrator" for a in res["agents"])

    # Builtin cannot be deleted
    r_del = client.delete("/api/agents/ag_pm")
    assert r_del.status_code == 400

    # Create custom agent
    created = client.post(
        "/api/agents",
        json={
            "name": "Test Bot",
            "avatar": "🧪",
            "description": "Test agent",
            "capabilities": ["testing"],
            "systemPrompt": "You are a test bot.",
            "adapterName": "custom",
            "modelProvider": "deepseek",
            "modelId": "deepseek-chat",
            "toolNames": ["fs_read"],
        },
    )
    assert created.status_code == 201
    aid = created.json()["agent"]["id"]

    # Update agent
    upd = client.patch(f"/api/agents/{aid}", json={"name": "Test Bot 2"})
    assert upd.status_code == 200
    assert upd.json()["agent"]["name"] == "Test Bot 2"

    # Delete custom agent
    assert client.delete(f"/api/agents/{aid}").status_code == 200


def test_conversation_fs_messages_search_and_artifacts(client: TestClient):
    # Create a mock agent directly in DB via API? adapterName enum in POST allows custom/claude-code/codex;
    # update or insert mock agent directly for testing MockAdapter end-to-end
    from app.db.client import db_tx

    with db_tx() as conn:
        conn.execute(
            """INSERT INTO agents (
                id, name, avatar, description, capabilities, system_prompt,
                adapter_name, model_provider, model_id, api_key, api_base_url,
                tool_names, is_builtin, is_orchestrator, supports_vision, created_at
            ) VALUES (
                'ag_mock', 'Mock Bot', '🤖', 'Mock', '[]', 'Mock prompt',
                'mock', NULL, NULL, NULL, NULL, '[]', 0, 0, 0, ?
            )""",
            (int(time.time() * 1000),),
        )

    conv_res = client.post("/api/conversations", json={"mode": "single", "agentIds": ["ag_mock"]})
    assert conv_res.status_code == 201
    conv_id = conv_res.json()["conversation"]["id"]

    # Patch title / pin / archive
    assert client.patch(f"/api/conversations/{conv_id}", json={"title": "Renamed"}).json()["conversation"]["title"] == "Renamed"
    assert client.patch(f"/api/conversations/{conv_id}", json={"togglePin": True}).json()["conversation"]["pinnedAt"] is not None
    assert client.patch(f"/api/conversations/{conv_id}", json={"toggleArchive": True}).json()["conversation"]["archived"] is True

    # Workspace fs write/read/listdir + traversal check
    w = client.post(f"/api/conversations/{conv_id}/fs/write", json={"path": "hello.txt", "content": "world"})
    assert w.status_code == 200
    r = client.get(f"/api/conversations/{conv_id}/fs/read", params={"path": "hello.txt"})
    assert r.status_code == 200 and r.json()["content"] == "world"
    ls = client.get(f"/api/conversations/{conv_id}/fs/listdir")
    assert ls.status_code == 200 and any(e["name"] == "hello.txt" for e in ls.json()["entries"])
    bad = client.get(f"/api/conversations/{conv_id}/fs/read", params={"path": "../../etc/passwd"})
    assert bad.status_code == 403

    # Send message to MockAdapter
    sent = client.post(f"/api/conversations/{conv_id}/messages", json={"content": "你好 Mock"})
    assert sent.status_code == 202
    assert len(sent.json()["runIds"]) == 1
    user_msg_id = sent.json()["messageId"]

    # Wait briefly for async mock stream to complete
    for _ in range(40):
        snap = client.get(f"/api/conversations/{conv_id}/messages").json()
        if len(snap["messages"]) >= 2 and snap["messages"][-1]["status"] == "complete":
            break
        time.sleep(0.05)

    snap = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(snap["messages"]) >= 2
    assert snap["messages"][-1]["status"] == "complete"
    assert "eventId" in snap

    # Bookmark & Pin
    bm = client.post(f"/api/messages/{user_msg_id}/bookmark", json={"conversationId": conv_id})
    assert bm.status_code == 200 and bm.json()["bookmarked"] is True
    pn = client.post(f"/api/messages/{user_msg_id}/pin", json={"conversationId": conv_id})
    assert pn.status_code == 200 and pn.json()["pinned"] is True

    # Search
    sr = client.get("/api/search", params={"q": "Mock", "fallback": "like"}).json()
    assert sr["ok"] is True and sr["data"]["total"] >= 1

    # Artifacts: create web_app + version + preview + export + deploy
    art = create_artifact(
        conversation_id=conv_id,
        art_type="web_app",
        title="Demo App",
        content={"type": "web_app", "files": {"index.html": "<h1>Hi</h1>"}, "entry": "index.html"},
        created_by_agent_id="ag_mock",
    )
    assert client.get(f"/api/artifacts/{art['id']}").status_code == 200
    assert client.get(f"/api/artifacts/{art['id']}/preview").status_code == 200
    assert client.get(f"/api/artifacts/{art['id']}/export").headers["content-type"] == "application/zip"

    v2 = client.post(
        f"/api/artifacts/{art['id']}/versions",
        json={"content": {"files": {"index.html": "<h1>Hi v2</h1>"}, "entry": "index.html"}},
    )
    assert v2.status_code == 200 and v2.json()["artifact"]["version"] == 2
    assert len(client.get(f"/api/artifacts/{art['id']}/versions").json()["versions"]) == 2

    dep = client.post(f"/api/conversations/{conv_id}/deploy", json={"artifactId": art["id"]})
    assert dep.status_code == 200
    preview_path = dep.json()["deployment"]["previewPath"]
    assert client.get(preview_path).status_code == 200

    # Usage summary & delete conversation
    usage = client.get("/api/usage/summary")
    assert usage.status_code == 200 and "allTime" in usage.json()
    assert client.delete(f"/api/conversations/{conv_id}").status_code == 200


def test_codex_adapter_stub():
    from app.adapters.codex_adapter import CodexAdapter
    from app.adapters.types import AdapterInput

    async def _run():
        adapter = CodexAdapter()
        inp = AdapterInput(
            agent_id="ag_1",
            conversation_id="conv_1",
            run_id="run_1",
            prompt="hi",
            workspace_path=".",
            system_prompt="",
            api_key=None,
            api_base_url=None,
            model_id=None,
            tool_names=[],
        )
        async for _ in adapter.stream(inp, asyncio.Event()):
            pass

    with pytest.raises(NotImplementedError, match="CodexAdapter"):
        asyncio.run(_run())
