import asyncio
import sys

import pytest

from app.tools import registry
from app.tools.types import ToolContext


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reload worker uses a Selector loop")
def test_bash_runs_in_windows_selector_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        registry, "get_workspace_for_conversation",
        lambda _: {"mode": "sandbox", "rootPath": str(tmp_path)},
    )

    async def echo():
        context = ToolContext("test", str(tmp_path), "test", "test", asyncio.Event())
        result = await registry.tool_registry.execute("bash", {"command": "echo hi"}, context)
        assert result.ok, repr(result.error)
        assert result.value["stdout"].strip() == "hi"
        assert result.value["cwd"] == str(tmp_path)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(echo())
