from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

import xhs_poster.cli as cli
from xhs_poster.models import PublishRunResult
from xhs_poster.publish.records import save_publish_evidence
from xhs_poster.publish.session import _step


class _FakePage:
    """最小桩：满足 save_publish_evidence 对 page 的两个调用点。"""

    def __init__(self) -> None:
        self.page = self

    def screenshot_on_failure(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG")

    def content(self) -> str:
        return "<html>evidence</html>"


class StepTimerTests(unittest.TestCase):
    def test_step_records_success(self) -> None:
        steps: list[dict] = []
        with _step("upload_images", steps, verbose=False):
            pass
        self.assertEqual(len(steps), 1)
        entry = steps[0]
        self.assertEqual(entry["step"], "upload_images")
        self.assertEqual(entry["status"], "success")
        self.assertIsInstance(entry["elapsed_ms"], int)
        self.assertGreaterEqual(entry["elapsed_ms"], 0)

    def test_step_records_failed_and_reraises(self) -> None:
        steps: list[dict] = []
        with self.assertRaises(ValueError):
            with _step("verify_success", steps, verbose=False):
                raise ValueError("boom")
        self.assertEqual(steps[0]["status"], "failed")
        self.assertEqual(steps[0]["step"], "verify_success")


class SaveEvidenceTests(unittest.TestCase):
    def test_writes_steps_jsonl(self) -> None:
        steps = [
            {"step": "upload_images", "status": "success", "elapsed_ms": 12},
            {"step": "verify_success", "status": "failed", "elapsed_ms": 34},
        ]
        with TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "p1-0-120000"
            evidence_dir.mkdir()
            artifacts = save_publish_evidence(_FakePage(), evidence_dir, steps=steps)

            self.assertTrue(Path(artifacts["screenshot"]).exists())
            self.assertTrue(Path(artifacts["html"]).exists())
            steps_path = Path(artifacts["steps"])
            self.assertTrue(steps_path.exists())
            lines = steps_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line) for line in lines], steps)

    def test_without_steps_omits_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "p1-0-120000"
            evidence_dir.mkdir()
            artifacts = save_publish_evidence(_FakePage(), evidence_dir)
            self.assertNotIn("steps", artifacts)
            self.assertFalse((evidence_dir / "steps.jsonl").exists())


class VerboseForwardingTests(unittest.TestCase):
    def test_verbose_flag_forwarded_to_run_publish_plan(self) -> None:
        captured: dict = {}

        def fake_run(**kwargs) -> PublishRunResult:
            captured.update(kwargs)
            return PublishRunResult(
                date="2026-06-02",
                mode="sequential",
                dedupe_scope="today",
                count_requested=0,
                count_selected=0,
                count_attempted=0,
                count_succeeded=0,
                count_failed=0,
            )

        original = cli.run_publish_plan
        cli.run_publish_plan = fake_run  # type: ignore[assignment]
        try:
            result = CliRunner().invoke(cli.app, ["publish", "--verbose"])
        finally:
            cli.run_publish_plan = original  # type: ignore[assignment]

        self.assertEqual(result.exit_code, 0)
        self.assertIs(captured.get("verbose"), True)

    def test_verbose_defaults_false(self) -> None:
        captured: dict = {}

        def fake_run(**kwargs) -> PublishRunResult:
            captured.update(kwargs)
            return PublishRunResult(
                date="2026-06-02",
                mode="sequential",
                dedupe_scope="today",
                count_requested=0,
                count_selected=0,
                count_attempted=0,
                count_succeeded=0,
                count_failed=0,
            )

        original = cli.run_publish_plan
        cli.run_publish_plan = fake_run  # type: ignore[assignment]
        try:
            CliRunner().invoke(cli.app, ["publish"])
        finally:
            cli.run_publish_plan = original  # type: ignore[assignment]

        self.assertIs(captured.get("verbose"), False)


if __name__ == "__main__":
    unittest.main()
