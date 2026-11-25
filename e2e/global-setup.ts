import { execFileSync } from 'node:child_process'
import { mkdirSync, rmSync } from 'node:fs'
import path from 'node:path'

const E2E_DATA_DIR = path.resolve('.agenthub-data-e2e')

/**
 * E2E 全局准备：建一个隔离的测试库（不碰真实 .agenthub-data），
 * 通过 FastAPI 后端的 bootstrap 初始化表和内置 agent，并插入一个 mock adapter 测试 agent。
 */
export default function globalSetup() {
  rmSync(E2E_DATA_DIR, { recursive: true, force: true })
  mkdirSync(path.join(E2E_DATA_DIR, 'workspaces'), { recursive: true })

  const pyScript = `
import json, time
from app.db.client import init_db, db_tx, close_db

init_db()
with db_tx() as conn:
    conn.execute(
        """INSERT INTO agents (
            id, name, avatar, description, capabilities, system_prompt,
            adapter_name, model_provider, model_id, api_key, api_base_url,
            tool_names, is_builtin, is_orchestrator, supports_vision, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "ag_e2e_mock",
            "E2E Mock",
            "🤖",
            "E2E 测试专用 mock agent（确定性脚本回复，不调真实 LLM）",
            json.dumps(["test"]),
            "mock",
            "mock",
            None,
            None,
            None,
            None,
            json.dumps([]),
            0,
            0,
            0,
            int(time.time() * 1000),
        ),
    )
close_db()
`

  execFileSync('uv', ['--directory', 'backend', 'run', 'python', '-c', pyScript], {
    stdio: 'inherit',
    env: {
      ...process.env,
      AGENTHUB_DATA_DIR: E2E_DATA_DIR,
    },
  })
}
