from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class Slot:
    week: int
    day: int
    period: int


@dataclass(frozen=True)
class Offering:
    course_code: str
    course_name: str
    teacher: str
    teaching_class: str
    schedule_text: str
    schedule_code: str
    avg_rating: float | None
    review_count: int | None
    slots: frozenset[Slot]
    score: float
    has_early_class: bool


@dataclass(frozen=True)
class SearchStats:
    raw_candidate_count: int
    compressed_candidate_count: int
    compressed_counts: dict[str, int]
    visited_nodes: int


@dataclass(frozen=True)
class Review:
    course_code: str
    course_name: str
    course_teacher: str
    rating: float | None
    score: str | None
    semester_name: str | None
    comment: str | None
    modified_at: str | None


@dataclass(frozen=True)
class TimetableResult:
    status: str
    requested_courses: list[str]
    selected: list[Offering]
    missing_courses: list[str]
    total_score: float | None
    average_score: float | None
    early_class_count: int
    max_early_classes: int | None
    stats: SearchStats
    warnings: list[str] = field(default_factory=list)


class ScheduleParseError(ValueError):
    pass


def load_input_config(input_path: Path) -> dict[str, Any]:
    return json.loads(input_path.read_text(encoding="utf-8"))


def load_requested_courses(input_path: Path) -> list[str]:
    return parse_requested_courses(load_input_config(input_path))


def parse_requested_courses(data: dict[str, Any]) -> list[str]:
    courses = data.get("compulsory")
    if not isinstance(courses, list) or not all(isinstance(course, str) for course in courses):
        raise ValueError("input JSON must contain a string list field named 'compulsory'.")
    return courses


def parse_max_early_classes(data: dict[str, Any]) -> int | None:
    value = data.get("max_early_classes")
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise ValueError("max_early_classes must be a non-negative integer.")
    return value


def parse_schedule_code(schedule_code: str) -> frozenset[Slot]:
    if not schedule_code:
        raise ScheduleParseError("empty schedule_code")
    if "RAW:" in schedule_code:
        raise ScheduleParseError(f"unparsed schedule_code: {schedule_code}")

    slots: set[Slot] = set()
    for item in filter(None, (part.strip() for part in schedule_code.split(";"))):
        match = re.fullmatch(r"D(\d+):(.*):(.*)", item)
        if not match:
            raise ScheduleParseError(f"invalid schedule component: {item}")
        day = int(match.group(1))
        periods = parse_number_expression(match.group(2), "P")
        weeks = parse_week_expression(match.group(3))
        for week in weeks:
            for period in periods:
                slots.add(Slot(week=week, day=day, period=period))

    if not slots:
        raise ScheduleParseError(f"schedule_code has no concrete slots: {schedule_code}")
    return frozenset(slots)


def parse_number_expression(text: str, prefix: str) -> set[int]:
    values: set[int] = set()
    cleaned = text.replace("，", "+").replace(",", "+")
    for part in filter(None, (item.strip() for item in cleaned.split("+"))):
        values.update(expand_number_range(part, prefix))
    if not values:
        raise ScheduleParseError(f"empty {prefix} expression: {text}")
    return values


def parse_week_expression(text: str) -> set[int]:
    parity: int | None = None
    if "单" in text:
        parity = 1
    elif "双" in text:
        parity = 0

    cleaned = re.sub(r"[（(][^）)]*[）)]", "", text)
    cleaned = cleaned.replace("周", "")
    values = parse_number_expression(cleaned, "W")
    if parity is not None:
        values = {week for week in values if week % 2 == parity}
    if not values:
        raise ScheduleParseError(f"empty week expression after parity filter: {text}")
    return values


def expand_number_range(text: str, prefix: str) -> set[int]:
    cleaned = text.strip()
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix) :]
    cleaned = cleaned.strip()
    if not cleaned:
        raise ScheduleParseError(f"empty range part: {text}")

    if "-" in cleaned:
        start_text, end_text = cleaned.split("-", 1)
        start = parse_int_token(start_text)
        end = parse_int_token(end_text)
        if end < start:
            raise ScheduleParseError(f"invalid descending range: {text}")
        return set(range(start, end + 1))
    return {parse_int_token(cleaned)}


def parse_int_token(text: str) -> int:
    match = re.search(r"\d+", text)
    if not match:
        raise ScheduleParseError(f"expected number in: {text}")
    return int(match.group(0))


def load_offerings(sqlite_path: Path, course_codes: list[str], unrated_score: float = 0.0) -> tuple[dict[str, list[Offering]], list[str]]:
    by_course = {course_code: [] for course_code in course_codes}
    warnings: list[str] = []
    if not course_codes:
        return by_course, warnings

    placeholders = ",".join("?" for _ in course_codes)
    query = f"""
        SELECT
            o.course_code,
            o.course_name,
            o.teacher,
            o.teaching_class,
            o.schedule_text,
            o.schedule_code,
            r.avg_rating,
            r.review_count
        FROM course_plus_offerings AS o
        LEFT JOIN course_teacher_rating_summary AS r
          ON r.course_code = o.course_code
         AND r.course_teacher = o.teacher
        WHERE o.course_code IN ({placeholders})
        ORDER BY o.course_code, o.teaching_class
    """

    with sqlite3.connect(sqlite_path) as connection:
        rows = connection.execute(query, course_codes).fetchall()

    for row in rows:
        course_code, course_name, teacher, teaching_class, schedule_text, schedule_code, avg_rating, review_count = row
        try:
            slots = parse_schedule_code(schedule_code or "")
        except ScheduleParseError as error:
            warnings.append(f"Skipped {course_code} {teaching_class}: {error}")
            continue

        score = float(avg_rating) if avg_rating is not None else unrated_score
        by_course[course_code].append(
            Offering(
                course_code=course_code,
                course_name=course_name,
                teacher=teacher,
                teaching_class=teaching_class,
                schedule_text=schedule_text,
                schedule_code=schedule_code,
                avg_rating=avg_rating,
                review_count=review_count,
                slots=slots,
                score=score,
                has_early_class=has_early_class(slots),
            )
        )

    return by_course, warnings


def compress_offerings(by_course: dict[str, list[Offering]]) -> dict[str, list[Offering]]:
    compressed: dict[str, list[Offering]] = {}
    for course_code, offerings in by_course.items():
        best_by_slots: dict[frozenset[Slot], Offering] = {}
        for offering in offerings:
            current = best_by_slots.get(offering.slots)
            if current is None or offering_sort_key(offering) > offering_sort_key(current):
                best_by_slots[offering.slots] = offering
        compressed[course_code] = sorted(best_by_slots.values(), key=offering_sort_key, reverse=True)
    return compressed


def offering_sort_key(offering: Offering) -> tuple[float, int, str]:
    return (offering.score, offering.review_count or 0, offering.teaching_class)


def has_early_class(slots: frozenset[Slot]) -> bool:
    return any(slot.period == 1 for slot in slots)


def build_timetable(
    input_path: Path,
    sqlite_path: Path,
    *,
    allow_missing: bool = False,
    unrated_score: float = 0.0,
    max_early_classes: int | None = None,
) -> TimetableResult:
    input_config = load_input_config(input_path)
    requested_courses = parse_requested_courses(input_config)
    if max_early_classes is None:
        max_early_classes = parse_max_early_classes(input_config)
    by_course, warnings = load_offerings(sqlite_path, requested_courses, unrated_score=unrated_score)
    raw_candidate_count = sum(len(offerings) for offerings in by_course.values())
    missing_courses = [course_code for course_code in requested_courses if not by_course[course_code]]

    if missing_courses and not allow_missing:
        stats = SearchStats(
            raw_candidate_count=raw_candidate_count,
            compressed_candidate_count=0,
            compressed_counts={},
            visited_nodes=0,
        )
        return TimetableResult(
            status="infeasible",
            requested_courses=requested_courses,
            selected=[],
            missing_courses=missing_courses,
            total_score=None,
            average_score=None,
            early_class_count=0,
            max_early_classes=max_early_classes,
            stats=stats,
            warnings=warnings,
        )

    searchable_courses = [course_code for course_code in requested_courses if by_course[course_code]]
    compressed = compress_offerings({course_code: by_course[course_code] for course_code in searchable_courses})
    selected, total_score, visited_nodes = search_best_timetable(compressed, max_early_classes=max_early_classes)
    compressed_counts = {course_code: len(offerings) for course_code, offerings in compressed.items()}
    compressed_candidate_count = sum(compressed_counts.values())
    stats = SearchStats(
        raw_candidate_count=raw_candidate_count,
        compressed_candidate_count=compressed_candidate_count,
        compressed_counts=compressed_counts,
        visited_nodes=visited_nodes,
    )

    if selected is None:
        return TimetableResult(
            status="infeasible",
            requested_courses=requested_courses,
            selected=[],
            missing_courses=missing_courses,
            total_score=None,
            average_score=None,
            early_class_count=0,
            max_early_classes=max_early_classes,
            stats=stats,
            warnings=warnings,
        )

    selected_by_course = {offering.course_code: offering for offering in selected}
    ordered_selected = [selected_by_course[course_code] for course_code in searchable_courses]
    early_class_count = sum(1 for offering in ordered_selected if offering.has_early_class)
    average_score = total_score / len(ordered_selected) if ordered_selected else None
    return TimetableResult(
        status="optimal",
        requested_courses=requested_courses,
        selected=ordered_selected,
        missing_courses=missing_courses,
        total_score=total_score,
        average_score=average_score,
        early_class_count=early_class_count,
        max_early_classes=max_early_classes,
        stats=stats,
        warnings=warnings,
    )


def search_best_timetable(
    by_course: dict[str, list[Offering]],
    *,
    max_early_classes: int | None = None,
) -> tuple[list[Offering] | None, float, int]:
    if not by_course:
        return [], 0.0, 1

    course_order = sorted(by_course, key=lambda course_code: len(by_course[course_code]))
    best_remaining = [0.0] * (len(course_order) + 1)
    for index in range(len(course_order) - 1, -1, -1):
        best_remaining[index] = best_remaining[index + 1] + by_course[course_order[index]][0].score

    best_selection: list[Offering] | None = None
    best_score = float("-inf")
    visited_nodes = 0

    def dfs(
        index: int,
        occupied: frozenset[Slot],
        selected: list[Offering],
        score: float,
        early_class_count: int,
    ) -> None:
        nonlocal best_selection, best_score, visited_nodes
        visited_nodes += 1
        if max_early_classes is not None and early_class_count > max_early_classes:
            return
        if score + best_remaining[index] < best_score:
            return
        if index == len(course_order):
            if score > best_score:
                best_score = score
                best_selection = list(selected)
            return

        course_code = course_order[index]
        for offering in by_course[course_code]:
            if occupied.isdisjoint(offering.slots):
                selected.append(offering)
                dfs(
                    index + 1,
                    occupied | offering.slots,
                    selected,
                    score + offering.score,
                    early_class_count + int(offering.has_early_class),
                )
                selected.pop()

    dfs(0, frozenset(), [], 0.0, 0)
    return best_selection, best_score, visited_nodes


def result_to_dict(result: TimetableResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "requested_courses": result.requested_courses,
        "missing_courses": result.missing_courses,
        "total_score": result.total_score,
        "average_score": result.average_score,
        "early_class_count": result.early_class_count,
        "max_early_classes": result.max_early_classes,
        "stats": {
            "raw_candidate_count": result.stats.raw_candidate_count,
            "compressed_candidate_count": result.stats.compressed_candidate_count,
            "compressed_counts": result.stats.compressed_counts,
            "visited_nodes": result.stats.visited_nodes,
        },
        "warnings": result.warnings,
        "selected": [offering_to_dict(offering) for offering in result.selected],
    }


def offering_to_dict(offering: Offering) -> dict[str, Any]:
    return {
        "course_code": offering.course_code,
        "course_name": offering.course_name,
        "teacher": offering.teacher,
        "teaching_class": offering.teaching_class,
        "schedule_text": offering.schedule_text,
        "schedule_code": offering.schedule_code,
        "avg_rating": offering.avg_rating,
        "review_count": offering.review_count,
        "score": offering.score,
        "has_early_class": offering.has_early_class,
    }


def fetch_reviews(sqlite_path: Path, offering: Offering, *, limit: int = 10, offset: int = 0) -> list[Review]:
    query = """
        SELECT
            course_code,
            course_name,
            course_teacher,
            rating,
            score,
            semester_name,
            comment,
            modified_at
        FROM course_teacher_reviews
        WHERE course_code = ?
          AND course_teacher = ?
        ORDER BY modified_at DESC
        LIMIT ? OFFSET ?
    """
    with sqlite3.connect(sqlite_path) as connection:
        rows = connection.execute(query, (offering.course_code, offering.teacher, limit, offset)).fetchall()

    return [
        Review(
            course_code=row[0],
            course_name=row[1],
            course_teacher=row[2],
            rating=row[3],
            score=row[4],
            semester_name=row[5],
            comment=row[6],
            modified_at=row[7],
        )
        for row in rows
    ]


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
    if result.missing_courses:
        lines.append(f"{c('Missing courses:', 'red', color)} {', '.join(result.missing_courses)}")
    if result.total_score is not None and result.average_score is not None:
        lines.append(f"{c('Total score:', 'bold', color)} {c(f'{result.total_score:.3f}', 'green', color)}")
        lines.append(f"{c('Average score:', 'bold', color)} {c(f'{result.average_score:.3f}', 'green', color)}")

    early_limit = "unlimited" if result.max_early_classes is None else str(result.max_early_classes)
    early_style = "green" if result.max_early_classes is None or result.early_class_count <= result.max_early_classes else "red"
    lines.append(f"{c('Early classes:', 'bold', color)} {c(f'{result.early_class_count} / {early_limit}', early_style, color)}")

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
            lines.append(
                f"{c(str(index), 'index', color)}. {c(offering.course_code, 'cyan', color)} {offering.course_name} | "
                f"{c(offering.teacher, 'magenta', color)} | {offering.teaching_class} | "
                f"rating={rating} reviews={reviews} early={early} | {c(offering.schedule_code, 'dim', color)}"
            )

    if result.warnings:
        lines.append("")
        lines.append(c("Warnings:", "yellow", color))
        for warning in result.warnings[:20]:
            lines.append(f"- {c(warning, 'yellow', color)}")
        if len(result.warnings) > 20:
            lines.append(f"- {c(f'... {len(result.warnings) - 20} more warnings', 'yellow', color)}")

    return "\n".join(lines)
