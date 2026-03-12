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
    status: Literal["partial", "complete"] = "complete"
    generated_at: str = ""
    products: list[ProductSummary]
    images: dict[str, list[str]]
    failed_products: list["ProductFailure"] = Field(default_factory=list)


Phase1RunStatus = Literal["running", "partial", "complete", "failed"]
Phase1FetchStatus = Literal["pending", "in_progress", "complete", "failed"]
Phase1ArtifactStatus = Literal["missing", "partial", "complete"]


class Phase1ImagesArtifact(BaseModel):
    status: Phase1ArtifactStatus = "missing"
    paths: list[str] = Field(default_factory=list)
    count: int = 0
    source: str = ""


class Phase1Artifacts(BaseModel):
    images: Phase1ImagesArtifact = Field(default_factory=Phase1ImagesArtifact)


class Phase1ProductState(BaseModel):
    product_id: str
    product_name: str
    list_discovered: bool = False
    fetch_status: Phase1FetchStatus = "pending"
    attempt_count: int = 0
    last_error: str | None = None
    updated_at: str = ""
    artifacts: Phase1Artifacts = Field(default_factory=Phase1Artifacts)


class Phase1State(BaseModel):
    date: str
    run_status: Phase1RunStatus = "running"
    started_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    target_total: int = 0
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    products: dict[str, Phase1ProductState] = Field(default_factory=dict)


class Phase1ExecutionResult(BaseModel):
    date: str
    run_status: Literal["complete", "partial"]
    progress_ref: str
    today_pool_path: str
    total_products: int
    success_count: int
    failed_count: int
    skipped_count: int
    failed_products: list["ProductFailure"] = Field(default_factory=list)
    today_pool: TodayPool


SiteName = Literal["merchant", "consumer"]
SessionStatus = Literal["authenticated", "login_required"]
BrowserMode = Literal["headless", "headful"]
AuthSource = Literal["auth_state", "profile", "missing"]


class SessionInfo(BaseModel):
    site: SiteName
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


class SkillError(BaseModel):
    status: Literal["error"] = "error"
    error: str
    message: str
    site: SiteName | None = None
    login: SessionInfo | None = None
    details: dict | None = None


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
    topic_keywords: list[str] = Field(default_factory=list)
    angle: int | None = None
    angle_name: str | None = None
    image_paths: list[str] = Field(default_factory=list)
    title_selector: str
    content_selector: str
    topic_results: list[dict] = Field(default_factory=list)
    product_binding: dict = Field(default_factory=dict)
    publish_result: dict = Field(default_factory=dict)
    log_path: str | None = None
    artifacts: dict | None = None


class Phase3Success(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase3ExecutionResult


Phase3DedupScope = Literal["today", "ever"]
Phase3PlanMode = Literal["sequential", "random"]
Phase3PlanItemStatus = Literal["pending", "published", "failed", "skipped"]
Phase3RecordStatus = Literal["success", "failed", "skipped"]


class Phase3PublishedRecord(BaseModel):
    date: str
    published_at: str
    product_id: str
    product_name: str
    angle: int
    angle_name: str | None = None
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    status: Literal["success"] = "success"
    publish_log_path: str | None = None
    dedupe_key: str


class Phase3PublishedLedger(BaseModel):
    records: list[Phase3PublishedRecord] = Field(default_factory=list)


class Phase3Candidate(BaseModel):
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


class Phase3CandidatesResult(BaseModel):
    date: str
    exclude_published: Phase3DedupScope
    candidates: list[Phase3Candidate] = Field(default_factory=list)


class Phase3PlanItem(BaseModel):
    sequence: int = 0
    product_id: str
    product_name: str
    angle: int
    angle_name: str
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    selection_reason: str
    status: Phase3PlanItemStatus = "pending"
    published_at: str | None = None
    error: str | None = None


class Phase3PlanResult(BaseModel):
    date: str
    mode: Phase3PlanMode
    dedupe_scope: Phase3DedupScope
    count_requested: int
    count_selected: int
    seed: int | None = None
    items: list[Phase3PlanItem] = Field(default_factory=list)
    plan_path: str | None = None


class Phase3PublishRecord(BaseModel):
    attempted_at: str
    product_id: str
    product_name: str
    angle: int
    angle_name: str | None = None
    title: str
    topic_keywords: list[str] = Field(default_factory=list)
    status: Phase3RecordStatus
    dedupe_key: str
    error: str | None = None
    publish_result: dict = Field(default_factory=dict)
    artifacts: dict | None = None


class Phase3DailyRecords(BaseModel):
    date: str
    records: list[Phase3PublishRecord] = Field(default_factory=list)


class Phase3RunPlanItemResult(BaseModel):
    product_id: str
    product_name: str
    angle: int
    angle_name: str
    status: Literal["success", "failed"]
    phase3_result: Phase3ExecutionResult | None = None
    error: str | None = None


class Phase3RunPlanResult(BaseModel):
    date: str
    mode: Phase3PlanMode
    dedupe_scope: Phase3DedupScope
    count_requested: int
    count_selected: int
    count_attempted: int
    count_succeeded: int
    count_failed: int
    seed: int | None = None
    results: list[Phase3RunPlanItemResult] = Field(default_factory=list)


class Phase3CandidatesSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase3CandidatesResult


class Phase3PlanSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase3PlanResult


class Phase3RunPlanSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    data: Phase3RunPlanResult


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


class Phase1Success(BaseModel):
    status: Literal["ok", "partial"] = "ok"
    data: Phase1ExecutionResult
