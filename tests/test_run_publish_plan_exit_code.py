from __future__ import annotations

import unittest

from typer.testing import CliRunner

import xhs_poster.cli as cli
from xhs_poster.models import PublishRunResult


def _make_result(*, attempted: int, succeeded: int, failed: int) -> PublishRunResult:
    return PublishRunResult(
        date="2026-06-02",
        mode="sequential",
        dedupe_scope="today",
        count_requested=attempted,
        count_selected=attempted,
        count_attempted=attempted,
        count_succeeded=succeeded,
        count_failed=failed,
    )


class RunPublishPlanExitCodeTests(unittest.TestCase):
    """发现 B：publish 的部分失败退出码语义。"""

    def setUp(self) -> None:
        self.runner = CliRunner()

    def _invoke_with(self, result: PublishRunResult) -> int:
        original = cli.run_publish_plan
        cli.run_publish_plan = lambda **_kwargs: result  # type: ignore[assignment]
        try:
            return self.runner.invoke(cli.app, ["publish"]).exit_code
        finally:
            cli.run_publish_plan = original  # type: ignore[assignment]

    def test_at_least_one_success_exits_zero(self) -> None:
        result = _make_result(attempted=3, succeeded=1, failed=2)
        self.assertEqual(self._invoke_with(result), 0)

    def test_no_pending_is_noop_exits_zero(self) -> None:
        result = _make_result(attempted=0, succeeded=0, failed=0)
        self.assertEqual(self._invoke_with(result), 0)

    def test_attempted_all_failed_exits_one(self) -> None:
        result = _make_result(attempted=2, succeeded=0, failed=2)
        self.assertEqual(self._invoke_with(result), 1)


if __name__ == "__main__":
    unittest.main()
