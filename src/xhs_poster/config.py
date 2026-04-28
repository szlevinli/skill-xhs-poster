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
    consumer_home_url: str = Field(
        default="https://www.xiaohongshu.com",
        description="消费者端首页 URL。",
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
    consumer_auth_state_path_override: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "XHS_POSTER_CONSUMER_AUTH_STATE_PATH",
            "CONSUMER_AUTH_STATE_PATH",
        ),
        description="用户端 auth-state 文件路径，默认位于 data_dir/auth/consumer-state.json。",
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
    def auth_dir(self) -> Path:
        return self.data_dir / "auth"

    @property
    def merchant_auth_state_path(self) -> Path:
        return self.merchant_auth_state_path_override or self.auth_dir / "merchant-state.json"

    @property
    def consumer_auth_state_path(self) -> Path:
        return self.consumer_auth_state_path_override or self.auth_dir / "consumer-state.json"

    @property
    def today_pool_path(self) -> Path:
        return self.data_dir / "today-pool.json"

    @property
    def phase1_state_path(self) -> Path:
        return self.data_dir / "phase1-state.json"

    @property
    def contents_path(self) -> Path:
        return self.data_dir / "contents.json"

    @property
    def product_facts_path(self) -> Path:
        return self.data_dir / "product-facts.json"

    @property
    def phase2_report_path(self) -> Path:
        return self.data_dir / "phase2-report.json"

    @property
    def image_semantic_facts_path(self) -> Path:
        return self.data_dir / "image-semantic-facts.json"

    @property
    def history_style_refs_path(self) -> Path:
        return self.data_dir / "history-style-refs.json"

    @property
    def trend_signals_path(self) -> Path:
        return self.data_dir / "trend-signals.json"

    @property
    def history_notes_dir(self) -> Path:
        return self.project_root / "references" / "history-notes"

    @property
    def publish_log_path(self) -> Path:
        return self.data_dir / "publish-log.json"

    @property
    def phase3_published_path(self) -> Path:
        return self.data_dir / "phase3-published.json"

    @property
    def publish_plan_path(self) -> Path:
        return self.data_dir / "publish-plan.json"

    @property
    def phase3_records_dir(self) -> Path:
        return self.data_dir / "phase3"

    def phase3_records_path(self, record_date: str) -> Path:
        return self.phase3_records_dir / record_date / "publish-records.json"

    @property
    def phase3_artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts" / "phase3"

    @property
    def image_facts_path(self) -> Path:
        return self.data_dir / "image-facts.json"

    @property
    def hot_notes_analysis_path(self) -> Path:
        return self.data_dir / "hot-notes-analysis.json"

    @property
    def raw_hot_notes_path(self) -> Path:
        return self.data_dir / "raw-hot-notes.json"

    @property
    def phase2_artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts" / "phase2"

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
        self.consumer_auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.merchant_profile_dir.mkdir(parents=True, exist_ok=True)
        self.consumer_profile_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.phase3_records_dir.mkdir(parents=True, exist_ok=True)
        self.phase3_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.phase2_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.auth_artifacts_dir.mkdir(parents=True, exist_ok=True)
