"""Tests for the PM -> Architect -> Backend agent pilot orchestration.

Run with: python3 -m unittest discover -s tests -v
All tests run in MOCK mode only — no network calls, no secrets needed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from company_os_cli.scaffold import scaffold  # noqa: E402

from company_os_agent_ui import pilot  # noqa: E402


def _tmp_dir(case: unittest.TestCase) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pilot-test-"))
    case.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
    return tmp


class ReadRunConfigTests(unittest.TestCase):
    def test_defaults_when_no_env_file(self) -> None:
        cfg = pilot.read_run_config(_tmp_dir(self))
        self.assertTrue(cfg.mock)
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.model, "openrouter/free")
        self.assertEqual(cfg.base_url, "https://openrouter.ai/api/v1")

    def test_reads_env_file(self) -> None:
        instance = _tmp_dir(self)
        self._write_env(instance, "MOCK_LLM=false\nOPENROUTER_API_KEY=sk-test\nOPENROUTER_MODEL=my/model\n")
        cfg = pilot.read_run_config(instance)
        self.assertFalse(cfg.mock)
        self.assertEqual(cfg.api_key, "sk-test")
        self.assertEqual(cfg.model, "my/model")

    def test_process_env_overrides_env_file(self) -> None:
        instance = _tmp_dir(self)
        self._write_env(instance, "MOCK_LLM=false\n")
        with mock.patch.dict(os.environ, {"MOCK_LLM": "true"}):
            cfg = pilot.read_run_config(instance)
        self.assertTrue(cfg.mock)

    @staticmethod
    def _write_env(instance: Path, content: str) -> None:
        runtime = instance / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / ".env").write_text(content, encoding="utf-8")


class LoadRolePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instance = _tmp_dir(self)
        scaffold(name="Acme", product="Acme App", out=self.instance)

    def test_loads_pm_prompt(self) -> None:
        prompt = pilot.load_role_prompt(self.instance, "pm")
        self.assertIn("Do not skip workflow gates", prompt)
        self.assertIn("## Responsibilities", prompt)

    def test_missing_role_raises(self) -> None:
        with self.assertRaises(pilot.PilotError):
            pilot.load_role_prompt(self.instance, "nonexistent-role")


class RunPilotTests(unittest.TestCase):
    def test_end_to_end_mock_run(self) -> None:
        instance = _tmp_dir(self)
        scaffold(name="Acme", product="Acme App", out=instance, slug="acme-app")

        events: list[dict] = []

        async def collect(evt: dict) -> None:
            events.append(evt)

        asyncio.run(pilot.run_pilot(instance, "Add a favorites feature", collect))

        types = [e["type"] for e in events]
        self.assertEqual(
            types,
            [
                "run_started",
                "stage_started", "stage_output",
                "meeting_started", "meeting_ended",
                "stage_started", "stage_output",
                "meeting_started", "meeting_ended",
                "stage_started", "stage_output",
                "run_completed",
            ],
        )
        self.assertTrue(events[0]["mock"])

        prd_path = instance / "projects" / "acme-app" / "prd" / "PRD-0000-pilot.md"
        arch_path = instance / "projects" / "acme-app" / "architecture" / "ARCH-0000-pilot.md"
        impl_path = instance / "projects" / "acme-app" / "api" / "IMPLEMENTATION-NOTES-0000-pilot.md"
        for path in (prd_path, arch_path, impl_path):
            self.assertTrue(path.is_file(), msg=str(path))
            self.assertGreater(len(path.read_text(encoding="utf-8")), 0)

    def test_unscaffolded_dir_raises_before_writing(self) -> None:
        instance = _tmp_dir(self)  # empty, no manifest
        events: list[dict] = []

        async def collect(evt: dict) -> None:
            events.append(evt)

        with self.assertRaises(pilot.PilotError):
            asyncio.run(pilot.run_pilot(instance, "Add a feature", collect))
        self.assertEqual(events, [])
        self.assertFalse((instance / "projects").exists())

    def test_empty_feature_request_rejected(self) -> None:
        instance = _tmp_dir(self)
        scaffold(name="Acme", product="Acme App", out=instance)

        async def collect(evt: dict) -> None:
            pass

        with self.assertRaises(pilot.PilotError):
            asyncio.run(pilot.run_pilot(instance, "   ", collect))

    def test_concurrent_run_rejected(self) -> None:
        instance = _tmp_dir(self)
        scaffold(name="Acme", product="Acme App", out=instance, slug="acme-app")
        events: list[dict] = []

        async def collect(evt: dict) -> None:
            events.append(evt)

        async def scenario() -> None:
            task = asyncio.create_task(pilot.run_pilot(instance, "First", collect))
            await asyncio.sleep(0)  # let the first run acquire the guard before we start
            with self.assertRaises(pilot.PilotError):
                await pilot.run_pilot(instance, "Second", collect)
            await task

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
