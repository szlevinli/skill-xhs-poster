from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProductSummary(BaseModel):
    id: str
    name: str


class DownloadedImage(BaseModel):
    index: int
    source_url: str
    path: str
    bytes: int
    format: str
    width: int
    height: int


class ProductImages(BaseModel):
    product_id: str
    product_name: str
    qimg_urls: list[str] = Field(default_factory=list)
    download_strategy: str = ""
    ci_domain_count: int = 0
    downloaded_images: list[DownloadedImage] = Field(default_factory=list)


class TodayPool(BaseModel):
    date: str
    products: list[ProductSummary]
    images: dict[str, list[str]]


SiteName = Literal["merchant", "consumer"]
SessionStatus = Literal["authenticated", "login_required"]
BrowserMode = Literal["headless", "headful"]


class SessionInfo(BaseModel):
    site: SiteName
    status: SessionStatus
    authenticated: bool
    browser_mode: BrowserMode
    checked_url: str
    profile_dir: str
    home_url: str
    message: str


class SkillError(BaseModel):
    status: Literal["error"] = "error"
    error: str
    message: str
    site: SiteName | None = None
    login: SessionInfo | None = None
    details: dict | None = None


class Phase1Success(BaseModel):
    status: Literal["ok"] = "ok"
    data: TodayPool


class ContentDraft(BaseModel):
    angle: int
    angle_name: str
    title: str
    content: str
    tags: str = ""
    reference_notes: list[str] = Field(default_factory=list)


class ContentGenerationMeta(BaseModel):
    source: str
    provider: str | None = None
    model: str | None = None
    error: str | None = None


class ContentsBundle(BaseModel):
    date: str
    total_products: int
    contents_per_product: int
    analysis_ref: str | None = None
    product_facts_ref: str | None = None
    phase2_report_ref: str | None = None
    contents: dict[str, list[ContentDraft]] = Field(default_factory=dict)
    generation: dict[str, ContentGenerationMeta] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    warnings: dict[str, list[str]] = Field(default_factory=dict)
    input_refs: dict[str, dict] = Field(default_factory=dict)


class ImageFact(BaseModel):
    path: str
    width: int
    height: int
    dominant_colors: list[str] = Field(default_factory=list)
    brightness: str = ""
    visual_elements: list[str] = Field(default_factory=list)


class ProductImageFacts(BaseModel):
    product_id: str
    product_name: str
    keywords: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    confirmed_elements: list[str] = Field(default_factory=list)
    images: list[ImageFact] = Field(default_factory=list)


class HotNote(BaseModel):
    note_id: str
    title: str
    url: str
    author: str = ""
    cover_url: str = ""
    like_count: int | None = None
    collect_count: int | None = None
    comment_count: int | None = None
    tags: list[str] = Field(default_factory=list)
    content: str = ""


class HotNotesAnalysis(BaseModel):
    keyword: str
    source: str
    total_collected: int = 0
    title_patterns: list[str] = Field(default_factory=list)
    content_patterns: list[str] = Field(default_factory=list)
    tag_candidates: list[str] = Field(default_factory=list)
    emoji_candidates: list[str] = Field(default_factory=list)
    scene_candidates: list[str] = Field(default_factory=list)
    tone_keywords: list[str] = Field(default_factory=list)
    notes: list[HotNote] = Field(default_factory=list)


class HistoryStyleReference(BaseModel):
    product_search_key: str
    title: str
    content: str
    hashtags: list[str] = Field(default_factory=list)
    cleaned_title: str = ""
    cleaned_content: str = ""
    cleaned_hashtags: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    quality_score: int = 0
    use_for_style: bool = False
    use_for_trend: bool = False
    filename_label: str = ""
    reference_terms: list[str] = Field(default_factory=list)
    cleaned_reference_terms: list[str] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)
    source_file: str


class ProductFactsSnapshot(BaseModel):
    product_id: str
    product_name: str
    image_paths: list[str] = Field(default_factory=list)
    visual_colors: list[str] = Field(default_factory=list)
    brightness_labels: list[str] = Field(default_factory=list)
    keyword_candidates: list[str] = Field(default_factory=list)
    style_candidates: list[str] = Field(default_factory=list)
    element_candidates: list[str] = Field(default_factory=list)
    history_style_refs: list[HistoryStyleReference] = Field(default_factory=list)
    trend_source: str | None = None
    trend_keywords: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductFailure(BaseModel):
    product_id: str
    product_name: str
    reason: str


class Phase2Report(BaseModel):
    date: str
    total_products: int
    success_count: int
    partial_count: int
    failed_count: int
    failed_products: list[ProductFailure] = Field(default_factory=list)
    template_products: list[str] = Field(default_factory=list)
    missing_history_products: list[str] = Field(default_factory=list)
    missing_trend_products: list[str] = Field(default_factory=list)
    warnings: dict[str, list[str]] = Field(default_factory=dict)


class Phase3ExecutionResult(BaseModel):
    product_id: str
    product_name: str
    title: str
    content: str
    topic_keyword: str | None = None
    angle: int | None = None
    angle_name: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    title_selector: str
    content_selector: str
    topic_result: dict | None = None
    product_binding: dict = Field(default_factory=dict)
    publish_result: dict = Field(default_factory=dict)
    log_path: str | None = None
    artifacts: dict | None = None


class Phase3Success(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase3ExecutionResult


class Phase2ExecutionResult(BaseModel):
    date: str
    keyword: str
    source: str
    total_products: int
    contents_per_product: int
    raw_hot_notes_path: str | None = None
    hot_notes_analysis_path: str
    image_facts_path: str
    product_facts_path: str | None = None
    phase2_report_path: str | None = None
    contents_path: str
    contents: dict[str, list[ContentDraft]] = Field(default_factory=dict)
    generation: dict[str, ContentGenerationMeta] = Field(default_factory=dict)
    statuses: dict[str, str] = Field(default_factory=dict)
    warnings: dict[str, list[str]] = Field(default_factory=dict)


class Phase2Success(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase2ExecutionResult
