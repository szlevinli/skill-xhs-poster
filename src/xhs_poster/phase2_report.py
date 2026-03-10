from __future__ import annotations

from datetime import date

from .models import Phase2Report, ProductFailure


def build_phase2_report(
    *,
    total_products: int,
    statuses: dict[str, str],
    warnings: dict[str, list[str]],
    failed_products: list[ProductFailure],
    generation_sources: dict[str, str],
) -> Phase2Report:
    success_count = sum(1 for status in statuses.values() if status == "ok")
    partial_count = sum(1 for status in statuses.values() if status == "partial")
    failed_count = sum(1 for status in statuses.values() if status == "failed")

    template_products = [
        product_id
        for product_id, source in generation_sources.items()
        if source in {"template", "llm_fallback"}
    ]
    missing_history_products = [
        product_id
        for product_id, items in warnings.items()
        if "未命中历史风格参考，已使用通用风格。" in items
    ]
    missing_trend_products = [
        product_id
        for product_id, items in warnings.items()
        if (
            "未提供趋势信号，已使用类目默认风格。" in items
            or "未提供独立趋势文件，已使用历史风格样本推导趋势信号。" in items
        )
    ]

    return Phase2Report(
        date=str(date.today()),
        total_products=total_products,
        success_count=success_count,
        partial_count=partial_count,
        failed_count=failed_count,
        failed_products=failed_products,
        template_products=template_products,
        missing_history_products=missing_history_products,
        missing_trend_products=missing_trend_products,
        warnings=warnings,
    )
