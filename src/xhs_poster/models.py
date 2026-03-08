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


class ContentsBundle(BaseModel):
    date: str
    total_products: int
    contents_per_product: int
    analysis_ref: str | None = None
    contents: dict[str, list[ContentDraft]] = Field(default_factory=dict)


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
