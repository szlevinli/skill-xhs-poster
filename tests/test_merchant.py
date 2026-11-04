from __future__ import annotations

import unittest
from unittest import mock

from xhs_poster.merchant import ProductListPage


class ProductListReadinessTests(unittest.TestCase):
    def test_clears_blocking_modal_before_waiting_for_rows(self) -> None:
        page = mock.Mock()
        calls: list[str] = []
        page.wait_for_url.side_effect = lambda *args, **kwargs: calls.append("url")
        page.wait_for_selector.side_effect = lambda *args, **kwargs: calls.append(
            "rows"
        )
        page.wait_for_timeout.side_effect = lambda *args, **kwargs: calls.append(
            "settle"
        )
        list_page = ProductListPage(page, mock.Mock())

        with mock.patch.object(
            list_page,
            "_clear_blocking_mask",
            side_effect=lambda: calls.append("clear") or True,
        ):
            list_page.wait_until_ready()

        self.assertEqual(calls, ["url", "clear", "rows", "settle"])


if __name__ == "__main__":
    unittest.main()
