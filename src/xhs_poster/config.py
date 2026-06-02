from __future__ import annotations

import sys
from pathlib import Path

from pydantic import AliasChoices, Field
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
    llm_base_url: str = Field(
        default="https://api.moonshot.cn/v1",
        validation_alias=AliasChoices("XHS_POSTER_LLM_BASE_URL", "LLM_BASE_URL", "MOONSHOT_BASE_URL"),
        description="LLM OpenAI 兼容接口 Base URL。",
    )
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("XHS_POSTER_LLM_API_KEY", "LLM_API_KEY", "MOONSHOT_API_KEY"),
        description="LLM API Key，支持通用和 Moonshot 命名。",
    )
    llm_model: str = Field(
        default="kimi-k2.6",
        validation_alias=AliasChoices("XHS_POSTER_LLM_MODEL", "LLM_MODEL", "MOONSHOT_MODEL"),
        description="LLM 模型名。",
    )
    vision_llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "XHS_POSTER_VISION_LLM_BASE_URL",
            "VISION_LLM_BASE_URL",
        ),
        description="视觉 LLM OpenAI 兼容接口 Base URL；未设置时复用 llm_base_url。",
    )
    vision_llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "XHS_POSTER_VISION_LLM_API_KEY",
            "VISION_LLM_API_KEY",
        ),
        description="视觉 LLM API Key；未设置时复用 llm_api_key。",
    )
    vision_llm_model: str | None = Field(
        default="moonshot-v1-8k-vision-preview",
        validation_alias=AliasChoices(
            "XHS_POSTER_VISION_LLM_MODEL",
            "VISION_LLM_MODEL",
        ),
        description="视觉 LLM 模型名；未设置时默认使用 Moonshot 视觉模型。",
    )
    playwright_browsers_path: Path = Field(
        default_factory=_default_playwright_browsers_path,
        description="Playwright 浏览器缓存路径，按平台自动选择默认值。",
    )
    merchant_auth_state_path_override: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "XHS_POSTER_MERCHANT_AUTH_STATE_PATH",
            "MERCHANT_AUTH_STATE_PATH",
        ),
        description="商家端 auth-state 文件路径，默认位于 data_dir/auth/merchant-state.json。",
    )
    publish_session_recycle_every: int = Field(
        default=10_000,
        validation_alias=AliasChoices(
            "XHS_POSTER_PUBLISH_SESSION_RECYCLE_EVERY",
            "PUBLISH_SESSION_RECYCLE_EVERY",
        ),
        description="发布每 N 篇后重建一次浏览器会话以平衡提速与风控；很大=整批共用一会话，1=每篇一会话。",
    )
    publish_interval_min_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices(
            "XHS_POSTER_PUBLISH_INTERVAL_MIN_SECONDS",
            "PUBLISH_INTERVAL_MIN_SECONDS",
        ),
        description="整批发布时每篇之间随机间隔的下限（秒），反检测用；min 与 max 同时 <=0 则关闭间隔。",
    )
    publish_interval_max_seconds: float = Field(
        default=90.0,
        validation_alias=AliasChoices(
            "XHS_POSTER_PUBLISH_INTERVAL_MAX_SECONDS",
            "PUBLISH_INTERVAL_MAX_SECONDS",
        ),
        description="整批发布时每篇之间随机间隔的上限（秒），反检测用；若 min>max 则 clamp 到 min。",
    )
    @property
    def data_dir(self) -> Path:
        return self.project_root / self.data_subdir

    @property
    def merchant_profile_dir(self) -> Path:
        return self.data_dir / "profiles" / "merchant"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"

    @property
    def merchant_auth_state_path(self) -> Path:
        return self.merchant_auth_state_path_override or self.auth_dir / "merchant-state.json"

    @property
    def products_path(self) -> Path:
        return self.data_dir / "products.json"

    @property
    def products_state_path(self) -> Path:
        return self.data_dir / "products-state.json"

    @property
    def contents_path(self) -> Path:
        return self.data_dir / "contents.json"

    @property
    def image_analysis_path(self) -> Path:
        return self.data_dir / "image-analysis.json"

    @property
    def publish_log_path(self) -> Path:
        return self.data_dir / "publish-log.json"

    @property
    def publish_plan_path(self) -> Path:
        return self.data_dir / "publish-plan.json"

    @property
    def publish_records_dir(self) -> Path:
        return self.data_dir / "publish"

    def publish_records_path(self, record_date: str) -> Path:
        return self.publish_records_dir / record_date / "records.json"

    def publish_evidence_dir(self, record_date: str) -> Path:
        return self.publish_records_dir / record_date / "evidence"

    @property
    def auth_artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts" / "auth"

    @property
    def resolved_vision_llm_base_url(self) -> str:
        return self.vision_llm_base_url or self.llm_base_url

    @property
    def resolved_vision_llm_api_key(self) -> str | None:
        return self.vision_llm_api_key or self.llm_api_key

    @property
    def resolved_vision_llm_model(self) -> str:
        return self.vision_llm_model or "moonshot-v1-8k-vision-preview"

    def merchant_edit_url(self, product_id: str) -> str:
        return self.merchant_edit_url_template.format(product_id=product_id)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.merchant_auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.merchant_profile_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.publish_records_dir.mkdir(parents=True, exist_ok=True)
        self.auth_artifacts_dir.mkdir(parents=True, exist_ok=True)
