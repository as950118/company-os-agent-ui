"""Smoke tests for the company-os-agent-ui web control panel.

Run with: python3 -m unittest discover -s tests -v
fastapi/httpx/etc. are mandatory dependencies of this package, so these
tests always run — no self-skip (contrast with the old company-os-cli[web]
extra this code was split out of).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from company_os_cli.scaffold import scaffold  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from company_os_agent_ui.app import create_app  # noqa: E402


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_index_serves_html(self) -> None:
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])
        self.assertIn("Company OS", res.text)

    def test_office_serves_html(self) -> None:
        res = self.client.get("/office")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])
        self.assertIn("Live Agent Pilot", res.text)

    def test_defaults_endpoint(self) -> None:
        res = self.client.get("/api/defaults")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("version", body)
        self.assertIn("cwd", body)
        self.assertIn("default_out", body)

    def test_api_init_success(self) -> None:
        out_dir = self._tmp_dir()
        res = self.client.post(
            "/api/init",
            json={"name": "Acme Agent Co", "product": "Acme Task Hub", "out": str(out_dir)},
        )
        self.assertEqual(res.status_code, 200, msg=res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["dest"], str(out_dir.resolve()))
        self.assertTrue((out_dir / "README.md").is_file())
        self.assertTrue((out_dir / "roles" / "pm.md").is_file())
        self.assertEqual(body["leftover"], [])

    def test_api_init_refuses_nonempty_dir(self) -> None:
        out_dir = self._tmp_dir()
        (out_dir / "keep.txt").write_text("existing file")

        res = self.client.post(
            "/api/init", json={"name": "Acme", "product": "Acme App", "out": str(out_dir)}
        )
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertIn("non-empty", body["error"])

    def test_api_init_force_overwrites(self) -> None:
        out_dir = self._tmp_dir()
        (out_dir / "keep.txt").write_text("existing file")

        res = self.client.post(
            "/api/init",
            json={"name": "Acme", "product": "Acme App", "out": str(out_dir), "force": True},
        )
        self.assertEqual(res.status_code, 200, msg=res.text)
        self.assertTrue((out_dir / "keep.txt").exists())
        self.assertTrue((out_dir / "README.md").exists())

    def test_api_init_missing_required_field_unified_error_shape(self) -> None:
        res = self.client.post("/api/init", json={"product": "Acme App"})
        self.assertEqual(res.status_code, 422)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertIn("name", body["error"])

    def test_api_upgrade_dry_run_reports_without_writing(self) -> None:
        out_dir = self._tmp_dir()
        self.client.post("/api/init", json={"name": "Acme", "product": "Acme App", "out": str(out_dir)})
        task_template = out_dir / "docs" / "task-template.md"
        task_template.unlink()

        res = self.client.post("/api/upgrade", json={"out": str(out_dir), "dry_run": True})
        self.assertEqual(res.status_code, 200, msg=res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertIn("docs/task-template.md", body["added"])
        self.assertFalse(task_template.exists())

    def test_api_upgrade_missing_dest_errors(self) -> None:
        res = self.client.post("/api/upgrade", json={"out": str(self._tmp_dir() / "does-not-exist")})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_ws_run_pilot_mock(self) -> None:
        out_dir = self._tmp_dir()
        scaffold(name="Acme", product="Acme App", out=out_dir, slug="acme-app")

        types = []
        with self.client.websocket_connect("/ws/run") as ws:
            ws.send_json(
                {"type": "start", "out": str(out_dir), "feature_request": "Add a favorites feature"}
            )
            while True:
                evt = ws.receive_json()
                types.append(evt["type"])
                if evt["type"] in ("run_completed", "error"):
                    break

        self.assertEqual(types[0], "run_started")
        self.assertEqual(types[-1], "run_completed")
        self.assertIn("stage_output", types)

    def test_api_board_missing_instance_errors(self) -> None:
        res = self.client.get("/api/board", params={"out": str(self._tmp_dir() / "does-not-exist")})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_api_doc_returns_task_markdown_and_fields(self) -> None:
        out_dir = self._tmp_dir()
        scaffold(name="Acme", product="Acme App", out=out_dir, slug="acme-app")
        (out_dir / "tasks" / "TASK-0001-favorites.md").write_text(
            "# Task: Add favorites\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Task ID | TASK-0001 |\n"
            "| Status | In Review |\n\n"
            "## Summary\n\nLet users star items.\n",
            encoding="utf-8",
        )

        res = self.client.get("/api/doc", params={"out": str(out_dir), "path": "tasks/TASK-0001-favorites.md"})
        self.assertEqual(res.status_code, 200, msg=res.text)
        body = res.json()
        self.assertEqual(body["title"], "Add favorites")
        self.assertEqual(body["fields"]["Status"], "In Review")
        self.assertIn("Let users star items.", body["markdown"])
        self.assertEqual(body["path"], "tasks/TASK-0001-favorites.md")

    def test_api_doc_rejects_paths_outside_instance_and_non_markdown(self) -> None:
        out_dir = self._tmp_dir()
        scaffold(name="Acme", product="Acme App", out=out_dir, slug="acme-app")
        secret = out_dir.parent / "secret.md"
        secret.write_text("# secret\n", encoding="utf-8")
        (out_dir / "tasks" / "link.md").symlink_to(secret)

        cases = {
            "../secret.md": 400,
            str(secret): 400,
            "tasks/link.md": 400,          # symlink pointing outside
            ".company-os-manifest.json": 400,
            "tasks/missing.md": 404,
            "": 400,
        }
        for path, expected in cases.items():
            res = self.client.get("/api/doc", params={"out": str(out_dir), "path": path})
            self.assertEqual(res.status_code, expected, msg=f"{path}: {res.text}")
            self.assertFalse(res.json()["ok"])

    def test_api_doc_missing_instance_errors(self) -> None:
        res = self.client.get("/api/doc", params={"out": str(self._tmp_dir() / "nope"), "path": "tasks/a.md"})
        self.assertEqual(res.status_code, 400)

    def test_api_board_reports_task_and_doc_status(self) -> None:
        out_dir = self._tmp_dir()
        scaffold(name="Acme", product="Acme App", out=out_dir, slug="acme-app")

        (out_dir / "tasks" / "TASK-0001-favorites.md").write_text(
            "# Task: Add favorites\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Task ID | TASK-0001 |\n"
            "| Type | Feature |\n"
            "| Status | In Review |\n",
            encoding="utf-8",
        )
        (out_dir / "tasks" / "TASK-0002-cleanup.md").write_text(
            "# Task: Cleanup\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| Task ID | TASK-0002 |\n"
            "| Status | Done |\n",
            encoding="utf-8",
        )
        prd_dir = out_dir / "projects" / "acme-app" / "prd"
        prd_dir.mkdir(parents=True, exist_ok=True)
        (prd_dir / "PRD-0001-favorites.md").write_text(
            "# PRD: Favorites\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| PRD ID | PRD-0001 |\n"
            "| Status | Draft |\n",
            encoding="utf-8",
        )

        res = self.client.get("/api/board", params={"out": str(out_dir)})
        self.assertEqual(res.status_code, 200, msg=res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["tasks"]["done"]), 1)
        self.assertEqual(body["tasks"]["done"][0]["id"], "TASK-0002")
        self.assertEqual(len(body["tasks"]["review"]), 1)
        self.assertEqual(body["tasks"]["review"][0]["id"], "TASK-0001")
        self.assertEqual(body["tasks"]["todo"], [])
        self.assertEqual(len(body["docs_needing_review"]), 1)
        self.assertEqual(body["docs_needing_review"][0]["id"], "PRD-0001")

    def test_ws_run_missing_instance_errors(self) -> None:
        with self.client.websocket_connect("/ws/run") as ws:
            ws.send_json(
                {"type": "start", "out": str(self._tmp_dir() / "nope"), "feature_request": "x"}
            )
            evt = ws.receive_json()

        self.assertEqual(evt["type"], "error")

    def _tmp_dir(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="company-os-agent-ui-test-"))
        self.addCleanup(self._cleanup, tmp)
        return tmp

    @staticmethod
    def _cleanup(path: Path) -> None:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


class CliTests(unittest.TestCase):
    def test_help_shows_options(self) -> None:
        import os

        # NO_COLOR/TERM=dumb: some CI runners force ANSI color even though
        # stdout is piped (not a tty), which would otherwise split
        # "--host"/"--port" across escape codes and break substring checks.
        env = {**os.environ, "PYTHONPATH": str(SRC_ROOT), "NO_COLOR": "1", "TERM": "dumb"}
        result = subprocess.run(
            [sys.executable, "-m", "company_os_agent_ui.cli", "--help"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("--host", result.stdout)
        self.assertIn("--port", result.stdout)


if __name__ == "__main__":
    unittest.main()
