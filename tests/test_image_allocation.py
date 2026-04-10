from __future__ import annotations

import unittest

from xhs_poster.image_allocation import allocate_image_paths


def _paths(count: int) -> list[str]:
    return [f"/tmp/{index}.jpg" for index in range(1, count + 1)]


class TestImageAllocation(unittest.TestCase):
    def test_allocate_image_paths_reuses_when_images_insufficient(self) -> None:
        allocations = allocate_image_paths(_paths(2), 5)

        self.assertEqual(len(allocations), 5)
        self.assertTrue(all(len(bucket) == 1 for bucket in allocations))
        self.assertEqual(allocations[0], ["/tmp/1.jpg"])
        self.assertEqual(allocations[1], ["/tmp/2.jpg"])
        self.assertEqual(allocations[2], ["/tmp/1.jpg"])

    def test_allocate_image_paths_balances_medium_pool(self) -> None:
        allocations = allocate_image_paths(_paths(10), 3)

        self.assertEqual([len(bucket) for bucket in allocations], [4, 3, 3])
        self.assertEqual(len({path for bucket in allocations for path in bucket}), 10)

    def test_allocate_image_paths_prefers_three_to_five_before_more(self) -> None:
        allocations = allocate_image_paths(_paths(18), 4)

        self.assertEqual([len(bucket) for bucket in allocations], [5, 5, 4, 4])
        self.assertLessEqual(max(len(bucket) for bucket in allocations), 5)

    def test_allocate_image_paths_hard_caps_each_draft_at_nine(self) -> None:
        allocations = allocate_image_paths(_paths(60), 4)

        self.assertEqual([len(bucket) for bucket in allocations], [9, 9, 9, 9])
        self.assertEqual(max(len(bucket) for bucket in allocations), 9)
