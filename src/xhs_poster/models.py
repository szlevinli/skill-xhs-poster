from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProductSummary(BaseModel):
    id: str
    name: str


class ProductImageAsset(BaseModel):
    image_id: str = ""
    path: str
    source_url: str = ""
    normalized_url: str = ""
    source_type: Literal["main", "detail", "unknown"] = "unknown"
    source_priority: int = 99
    position: int = 0
    bytes: int = 0
    format: str = ""
    width: int = 0
    height: int = 0
    sha256: str = ""
    is_original: bool = True


class DownloadedImage(ProductImageAsset):
    index: int


class ProductImages(BaseModel):
    product_id: str
    product_name: str
    qimg_urls: list[str] = Field(default_factory=list)
    download_strategy: str = ""
    ci_domain_count: int = 0
    downloaded_images: list[DownloadedImage] = Field(default_factory=list)


class TodayPool(BaseModel):
    date: str
    status: Literal["partial", "complete"] = "complete"
    generated_at: str = ""
    products: list[ProductSummary]
    images: dict[str, list[str]] = Field(default_factory=dict)
    image_assets: dict[str, list[ProductImageAsset]] = Field(default_factory=dict)
    failed_products: list["ProductFailure"] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_compatibility_views(self) -> "TodayPool":
        if not self.images and self.image_assets:
            self.images = {
                product_id: [asset.path for asset in assets]
                for product_id, assets in self.image_assets.items()
            }
        return self


ProductsRunStatus = Literal["running", "partial", "complete", "failed"]
ProductFetchStatus = Literal["pending", "in_progress", "complete", "failed"]
ProductArtifactStatus = Literal["missing", "partial", "complete"]


class ProductImagesArtifact(BaseModel):
    status: ProductArtifactStatus = "missing"
    paths: list[str] = Field(default_factory=list)
    assets: list[ProductImageAsset] = Field(default_factory=list)
    count: int = 0
    source: str = ""

    @model_validator(mode="after")
    def _sync_assets(self) -> "ProductImagesArtifact":
        if not self.paths and self.assets:
            self.paths = [asset.path for asset in self.assets]
        if not self.count:
            self.count = len(self.assets or self.paths)
        return self


class ProductArtifacts(BaseModel):
    images: ProductImagesArtifact = Field(default_factory=ProductImagesArtifact)


class ProductFetchState(BaseModel):
    product_id: str
    product_name: str
    list_discovered: bool = False
    fetch_status: ProductFetchStatus = "pending"
    attempt_count: int = 0
    last_error: str | None = None
    updated_at: str = ""
    artifacts: ProductArtifacts = Field(default_factory=ProductArtifacts)


class ProductsState(BaseModel):
    date: str
    run_status: ProductsRunStatus = "running"
    started_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    target_total: int = 0
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    products: dict[str, ProductFetchState] = Field(default_factory=dict)


class FetchProductsExecutionResult(BaseModel):
    date: str
    run_status: Literal["complete", "partial"]
    progress_ref: str
    products_path: str
    total_products: int
    success_count: int
    failed_count: int
    skipped_count: int
    failed_products: list["ProductFailure"] = Field(default_factory=list)
    today_pool: TodayPool
    warnings: list[str] = Field(default_factory=list)


SessionStatus = Literal["authenticated", "login_required"]
BrowserMode = Literal["headless", "headful"]
AuthSource = Literal["auth_state", "profile", "missing"]


class SessionInfo(BaseModel):
    site: Literal["merchant"]
    status: SessionStatus
    authenticated: bool
    auth_source: AuthSource
    attempted_auth_sources: list[AuthSource] = Field(default_factory=list)
    browser_mode: BrowserMode
    checked_url: str
    profile_dir: str
    auth_state_path: str | None = None
    home_url: str
    message: str


class ContentDraft(BaseModel):
    angle: int
    angle_name: str
    title: str
    content: str
    tags: str = ""
    reference_notes: list[str] = Field(default_factory=list)
    selected_image_paths: list[str] = Field(default_factory=list)
    selected_image_count: int = 0


class ContentGenerationMeta(BaseModel):
    source: str
    provider: str | None = None
    model: str | None = None
    error: str | None = None


class ContentsBundle(BaseModel):
    date: str
    total_products: int
    contents_per_product: int
    contents: dict[str, list[ContentDraft]] = Field(default_factory=dict)
    generation: dict[str, ContentGenerationMeta] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    warnings: dict[str, list[str]] = Field(default_factory=dict)



ImageSemanticStatus = Literal["success", "failed"]


class ImageSemanticFact(BaseModel):
    image_sha256: str
    path: str
    width: int
    height: int
    model: str
    analyzed_at: str
    status: ImageSemanticStatus
    summary: str = ""
    category: str = ""
    colors: list[str] = Field(default_factory=list)
    material_guesses: list[str] = Field(default_factory=list)
    visible_elements: list[str] = Field(default_factory=list)
    product_elements: list[str] = Field(default_factory=list)
    background_elements: list[str] = Field(default_factory=list)
    style_moods: list[str] = Field(default_factory=list)
    scene_guesses: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    error: str | None = None
    raw_text: str | None = None


class ImageSemanticFactsBundle(BaseModel):
    date: str
    source: str = "vision_llm"
    items: list[ImageSemanticFact] = Field(default_factory=list)


class ProductSemanticFacts(BaseModel):
    product_id: str
    product_name: str
    image_count: int = 0
    summary: str = ""
    categories: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    material_guesses: list[str] = Field(default_factory=list)
    visible_elements: list[str] = Field(default_factory=list)
    product_elements: list[str] = Field(default_factory=list)
    background_elements: list[str] = Field(default_factory=list)
    style_moods: list[str] = Field(default_factory=list)
    scene_guesses: list[str] = Field(default_factory=list)
    confidence_notes: list[str] = Field(default_factory=list)
    images: list[ImageSemanticFact] = Field(default_factory=list)


class ProductFailure(BaseModel):
    product_id: str
    product_name: str
    reason: str


class PublishExecutionResult(BaseModel):
    product_id: str
    product_name: str
    title: str
    content: str
    topic_keywords: list[str] = Field(default_factory=list)
    angle: int | None = None
    angle_name: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    title_selector: str
    content_selector: str
    topic_results: list[dict] = Field(default_factory=list)
    skipped_topics: list[dict] = Field(default_factory=list)
    product_binding: dict = Field(default_factory=dict)
    publish_result: dict = Field(default_factory=dict)
    log_path: str | None = None
    artifacts: dict | None = None


PublishDedupScope = Literal["today", "ever"]
PublishPlanMode = Literal["sequential", "random"]
PublishPlanItemStatus = Literal["pending", "published", "failed", "skipped"]
PublishRecordStatus = Literal["success", "failed", "skipped"]


class PublishCandidate(BaseModel):
    date: str
    product_id: str
    product_name: str
    angle: int
    angle_name: str
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    image_count: int = 0
    published_today: bool = False
    published_ever: bool = False
    eligible: bool = True
    ineligible_reason: str | None = None


class PublishCandidatesResult(BaseModel):
    date: str
    exclude_published: PublishDedupScope
    candidates: list[PublishCandidate] = Field(default_factory=list)


class PublishPlanItem(BaseModel):
    sequence: int = 0
    product_id: str
    product_name: str
    angle: int
    angle_name: str
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    selection_reason: str
    status: PublishPlanItemStatus = "pending"
    published_at: str | None = None
    error: str | None = None


class PublishPlanResult(BaseModel):
    date: str
    mode: PublishPlanMode
    dedupe_scope: PublishDedupScope
    count_requested: int
    count_selected: int
    seed: int | None = None
    items: list[PublishPlanItem] = Field(default_factory=list)
    plan_path: str | None = None


class PublishRecord(BaseModel):
    attempted_at: str
    product_id: str
    product_name: str
    angle: int
    angle_name: str | None = None
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    skipped_topics: list[dict] = Field(default_factory=list)
    status: PublishRecordStatus
    dedupe_key: str
    error: str | None = None
    publish_result: dict = Field(default_factory=dict)
    artifacts: dict | None = None


class PublishDailyRecords(BaseModel):
    date: str
    records: list[PublishRecord] = Field(default_factory=list)


class PublishRunItemResult(BaseModel):
    product_id: str
    product_name: str
    angle: int
    angle_name: str
    status: Literal["success", "failed"]
    execution_result: PublishExecutionResult | None = None
    error: str | None = None


class PublishRunResult(BaseModel):
    date: str
    mode: PublishPlanMode
    dedupe_scope: PublishDedupScope
    count_requested: int
    count_selected: int
    count_attempted: int
    count_succeeded: int
    count_failed: int
    seed: int | None = None
    results: list[PublishRunItemResult] = Field(default_factory=list)


class GenerateContentExecutionResult(BaseModel):
    date: str
    total_products: int
    contents_per_product: int
    image_analysis_path: str | None = None
    contents_path: str
    contents: dict[str, list[ContentDraft]] = Field(default_factory=dict)
    generation: dict[str, ContentGenerationMeta] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    warnings: dict[str, list[str]] = Field(default_factory=dict)
