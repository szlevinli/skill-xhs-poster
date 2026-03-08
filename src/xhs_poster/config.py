from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_playwright_browsers_path() -> Path:
    """Playwright 各平台默认缓存路径。"""
    home = Path.home()
    if sys.platform == "win32":
        return home / "AppData" / "Local" / "ms-playwright"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


class Settings(BaseSettings):
    """配置项：支持环境变量与 .env 覆盖，前缀 XHS_POSTER_。"""

    model_config = SettingsConfigDict(
        env_prefix="XHS_POSTER_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    project_root: Path = Field(
        default=PROJECT_ROOT,
        description="项目根目录，数据与配置相对此路径。",
    )
    data_subdir: str = Field(
        default="xiaohongshu-data",
        description="数据子目录名，相对于 project_root。",
    )
    merchant_home_url: str = Field(
        default="https://ark.xiaohongshu.com/app-system/home",
        description="商家工作台首页 URL。",
    )
    merchant_list_url: str = Field(
        default="https://ark.xiaohongshu.com/app-item/list/shelf",
        description="商品列表页 URL。",
    )
    merchant_edit_url_template: str = Field(
        default="https://ark.xiaohongshu.com/app-item/good/edit/{product_id}",
        description="商品编辑页 URL 模板，{product_id} 会被替换。",
    )
    consumer_home_url: str = Field(
        default="https://www.xiaohongshu.com",
        description="消费者端首页 URL。",
    )
    playwright_browsers_path: Path = Field(
        default_factory=_default_playwright_browsers_path,
        description="Playwright 浏览器缓存路径，按平台自动选择默认值。",
    )

    @property
    def data_dir(self) -> Path:
        return self.project_root / self.data_subdir

    @property
    def merchant_profile_dir(self) -> Path:
        return self.data_dir / "profiles" / "merchant"

    @property
    def consumer_profile_dir(self) -> Path:
        return self.data_dir / "profiles" / "consumer"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def today_pool_path(self) -> Path:
        return self.data_dir / "today-pool.json"

    def merchant_edit_url(self, product_id: str) -> str:
        return self.merchant_edit_url_template.format(product_id=product_id)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.merchant_profile_dir.mkdir(parents=True, exist_ok=True)
        self.consumer_profile_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
