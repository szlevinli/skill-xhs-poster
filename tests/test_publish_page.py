from __future__ import annotations

import unittest
from unittest import mock

from xhs_poster.publish.page import PublishPage


class _FakeLocator:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.clicked = False
        self.filled: list[str] = []
        self.pressed: list[str] = []

    @property
    def first(self) -> "_FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self.visible

    def wait_for(self, *, state: str, timeout: int) -> None:
        if not self.visible:
            raise RuntimeError("locator not visible")

    def click(self) -> None:
        self.clicked = True

    def fill(self, value: str) -> None:
        self.filled.append(value)

    def press(self, key: str) -> None:
        self.pressed.append(key)


class _FakePage:
    def __init__(self, placeholder: str) -> None:
        self.placeholder = placeholder
        self.search_input: _FakeLocator | None = None

    def get_by_text(self, text: str, **kwargs) -> _FakeLocator:
        return _FakeLocator(text == "添加商品")

    def get_by_placeholder(self, expected) -> _FakeLocator:
        matched = (
            expected == self.placeholder
            if isinstance(expected, str)
            else expected.fullmatch(self.placeholder) is not None
        )
        self.search_input = _FakeLocator(matched)
        return self.search_input


class AddProductSearchInputTests(unittest.TestCase):
    def test_open_dialog_accepts_old_and_new_placeholders(self) -> None:
        for placeholder in ("搜索商品ID 或 商品名称", "请输入商品ID/名称"):
            with self.subTest(placeholder=placeholder):
                publish_page = PublishPage(_FakePage(placeholder), mock.Mock())
                publish_page._open_add_product_dialog()

    def test_add_product_accepts_old_and_new_placeholders(self) -> None:
        for placeholder in ("搜索商品ID 或 商品名称", "请输入商品ID/名称"):
            with self.subTest(placeholder=placeholder):
                page = _FakePage(placeholder)
                publish_page = PublishPage(page, mock.Mock())
                with (
                    mock.patch.object(publish_page, "_open_add_product_dialog"),
                    mock.patch.object(
                        publish_page,
                        "_click_product_candidate",
                        return_value={"found": True},
                    ),
                    mock.patch.object(
                        publish_page,
                        "_click_add_product_dialog_save",
                        return_value=True,
                    ),
                    mock.patch.object(
                        publish_page,
                        "_verify_product_bound",
                        return_value={"bound": True, "modal_still_visible": False},
                    ),
                ):
                    result = publish_page.add_product("product-1")

                self.assertTrue(result["search_box_found"])
                self.assertIsNotNone(page.search_input)
                assert page.search_input is not None
                self.assertEqual(page.search_input.filled, ["", "product-1"])
                self.assertEqual(page.search_input.pressed, ["Enter"])


if __name__ == "__main__":
    unittest.main()
