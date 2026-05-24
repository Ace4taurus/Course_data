from __future__ import annotations

from sjtu_course_analysis.scheduler import Review, TimetableResult


COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "header": "\033[1;36m",
    "index": "\033[1;33m",
}


def c(text: str, style: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{COLORS[style]}{text}{COLORS['reset']}"


def _color_rating(text: str, rating: float | None, enabled: bool) -> str:
    if rating is None:
        return c(text, "dim", enabled)
    if rating >= 4.5:
        return c(text, "green", enabled)
    if rating >= 3.5:
        return c(text, "yellow", enabled)
    return c(text, "red", enabled)


def format_reviews(reviews: list[Review], *, page: int, page_size: int = 10, color: bool = False) -> str:
    if not reviews:
        return "没有更多评论。"

    lines = [c(f"评论第 {page} 页：", "header", color)]
    start = (page - 1) * page_size
    for index, review in enumerate(reviews, start=start + 1):
        rating_text = "无评分" if review.rating is None else f"{review.rating:.1f}"
        rating_colored = _color_rating(rating_text, review.rating, color)
        semester = review.semester_name or "未知学期"
        modified_at = review.modified_at or "未知时间"
        comment = (review.comment or "").strip() or "无文字评论"
        idx = c(str(index), "index", color)
        sem = c(f"[{semester}]", "dim", color)
        lines.append(f"{idx}. {sem} rating={rating_colored} score={review.score or '无'} updated={modified_at}")
        lines.append(f"  {comment}")
    return "\n".join(lines)


def format_result(result: TimetableResult, *, color: bool = False) -> str:
    status_style = "green" if result.status == "optimal" else "red"
    lines = [f"{c('Status:', 'bold', color)} {c(result.status, status_style, color)}"]
    if result.missing_compulsory_courses:
        lines.append(f"{c('Missing compulsory courses:', 'red', color)} {', '.join(result.missing_compulsory_courses)}")
    if result.missing_optional_courses:
        lines.append(f"{c('Missing optional courses:', 'yellow', color)} {', '.join(result.missing_optional_courses)}")
    if result.weighted_average_score is not None:
        lines.append(f"{c('Weighted average score:', 'bold', color)} {c(f'{result.weighted_average_score:.3f}', 'green', color)}")
    if result.weighted_score_sum is not None and result.total_credits is not None:
        lines.append(f"{c('Weighted score sum:', 'bold', color)} {c(f'{result.weighted_score_sum:.3f}', 'green', color)}")
        lines.append(f"{c('Total credits:', 'bold', color)} {c(f'{result.total_credits:.1f}', 'green', color)}")

    early_limit = "unlimited" if result.max_early_classes is None else str(result.max_early_classes)
    early_style = "green" if result.max_early_classes is None or result.early_class_count <= result.max_early_classes else "red"
    lines.append(f"{c('Early classes:', 'bold', color)} {c(f'{result.early_class_count} / {early_limit}', early_style, color)}")

    if result.selected_optional_courses:
        lines.append(f"{c('Selected optional courses:', 'bold', color)} {', '.join(result.selected_optional_courses)}")
    if result.skipped_optional_courses:
        lines.append(f"{c('Skipped optional courses:', 'yellow', color)} {', '.join(result.skipped_optional_courses)}")

    lines.append(
        f"{c('Stats:', 'dim', color)} "
        f"raw={result.stats.raw_candidate_count}, "
        f"compressed={result.stats.compressed_candidate_count}, "
        f"visited_nodes={result.stats.visited_nodes}"
    )

    if result.stats.compressed_counts:
        counts = ", ".join(f"{course}={count}" for course, count in result.stats.compressed_counts.items())
        lines.append(f"{c('Compressed counts:', 'dim', color)} {counts}")

    if result.selected:
        lines.append("")
        lines.append(c("Selected offerings:", "header", color))
        for index, offering in enumerate(result.selected, start=1):
            rating_text = "unrated" if offering.avg_rating is None else f"{offering.avg_rating:.3f}"
            rating = _color_rating(rating_text, offering.avg_rating, color)
            reviews = 0 if offering.review_count is None else offering.review_count
            early = c("yes", "yellow", color) if offering.has_early_class else c("no", "green", color)
            course_type = c("[选修]", "yellow", color) if offering.course_code in result.optional_courses else c("[必修]", "cyan", color)
            lines.append(
                f"{c(str(index), 'index', color)}. {course_type} {c(offering.course_code, 'cyan', color)} {offering.course_name} | "
                f"{c(offering.teacher, 'magenta', color)} | {offering.teaching_class} | "
                f"credits={offering.credits:.1f} rating={rating} reviews={reviews} early={early} | {c(offering.schedule_code, 'dim', color)}"
            )

    if result.warnings:
        lines.append("")
        lines.append(c("Warnings:", "yellow", color))
        for warning in result.warnings[:20]:
            lines.append(f"- {c(warning, 'yellow', color)}")
        if len(result.warnings) > 20:
            lines.append(f"- {c(f'... {len(result.warnings) - 20} more warnings', 'yellow', color)}")

    return "\n".join(lines)
