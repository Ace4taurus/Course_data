from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


TEACHER_SEPARATORS = str.maketrans(
    {
        "、": ",",
        "，": ",",
        "；": ",",
        ";": ",",
        "/": ",",
        "／": ",",
        "|": ",",
        "｜": ",",
        "&": ",",
        "＆": ",",
    }
)
PLACEHOLDER_TEACHERS = {"", "nan", "none", "null", "未知", "暂无", "待定"}


OFFERING_COLUMNS = [
    "course_code",
    "course_name",
    "department",
    "teacher",
    "credits",
    "enrollment",
    "class_capacity",
    "teaching_class",
    "campus",
    "course_type",
    "schedule_text",
    "has_virtual_schedule",
    "is_weekend_course",
    "time_bucket",
]


REVIEW_COLUMNS = [
    "review_id",
    "course_id",
    "course_code",
    "course_name",
    "course_teacher",
    "rating",
    "score",
    "semester_name",
    "comment",
    "comment_length",
    "created_at",
    "modified_at",
]


CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_courses_name ON courses(course_name)",
    "CREATE INDEX IF NOT EXISTS idx_offerings_course ON offerings(course_code)",
    "CREATE INDEX IF NOT EXISTS idx_offerings_course_teacher_raw ON offerings(course_code, teacher_raw)",
    "CREATE INDEX IF NOT EXISTS idx_offering_teachers_course_teacher ON offering_teachers(course_code, teacher_norm)",
    "CREATE INDEX IF NOT EXISTS idx_community_reviews_review_id ON community_reviews(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_community_reviews_course ON community_reviews(course_code)",
    "CREATE INDEX IF NOT EXISTS idx_community_reviews_variant ON community_reviews(community_course_id)",
    "CREATE INDEX IF NOT EXISTS idx_community_variant_teachers_course_teacher ON community_course_variant_teachers(course_code, teacher_norm)",
]


DROP_VIEWS = [
    "DROP VIEW IF EXISTS v_course_offerings_with_review_summary",
    "DROP VIEW IF EXISTS v_course_teacher_reviews",
    "DROP VIEW IF EXISTS v_course_offerings",
    "DROP VIEW IF EXISTS v_course_search",
]


CREATE_VIEWS = [
    """
    CREATE VIEW v_course_search AS
    SELECT
      c.course_code,
      c.course_name,
      c.department,
      c.credits,
      COUNT(DISTINCT o.offering_id) AS offering_count,
      COUNT(DISTINCT ot.teacher_norm) AS teacher_count
    FROM courses c
    LEFT JOIN offerings o ON c.course_code = o.course_code
    LEFT JOIN offering_teachers ot ON o.offering_id = ot.offering_id
    GROUP BY c.course_code, c.course_name, c.department, c.credits
    """,
    """
    CREATE VIEW v_course_offerings AS
    SELECT
      o.offering_id,
      o.semester,
      o.course_code,
      o.course_name,
      o.department,
      o.teacher_raw,
      o.credits,
      o.enrollment,
      o.class_capacity,
      o.teaching_class,
      o.campus,
      o.course_type,
      o.schedule_text,
      o.has_virtual_schedule,
      o.is_weekend_course,
      o.time_bucket
    FROM offerings o
    """,
    """
    CREATE VIEW v_course_teacher_reviews AS
    SELECT
      r.review_id,
      r.community_course_id,
      r.course_code,
      r.course_name,
      r.course_teacher_raw,
      vt.teacher_norm,
      r.rating,
      r.score,
      r.semester_name,
      r.comment,
      r.comment_length,
      r.created_at,
      r.modified_at
    FROM community_reviews r
    JOIN community_course_variant_teachers vt
      ON r.community_course_id = vt.community_course_id
    """,
    """
    CREATE VIEW v_course_offerings_with_review_summary AS
    SELECT
      o.offering_id,
      o.semester,
      o.course_code,
      o.course_name,
      o.teacher_raw,
      ot.teacher_norm,
      o.teaching_class,
      o.campus,
      o.schedule_text,
      o.time_bucket,
      COUNT(r.review_id) AS review_count,
      ROUND(AVG(r.rating), 3) AS avg_rating,
      ROUND(AVG(r.comment_length), 1) AS avg_comment_length,
      MAX(r.modified_at) AS latest_review_at
    FROM offerings o
    JOIN offering_teachers ot
      ON o.offering_id = ot.offering_id
    LEFT JOIN community_course_variant_teachers cvt
      ON o.course_code = cvt.course_code
     AND ot.teacher_norm = cvt.teacher_norm
    LEFT JOIN community_reviews r
      ON cvt.community_course_id = r.community_course_id
    GROUP BY
      o.offering_id,
      o.semester,
      o.course_code,
      o.course_name,
      o.teacher_raw,
      ot.teacher_norm,
      o.teaching_class,
      o.campus,
      o.schedule_text,
      o.time_bucket
    """,
]


def export_course_lookup_sqlite(
    db_path: Path,
    semester: str,
    course_plus_offerings: pd.DataFrame,
    community_reviews: pd.DataFrame,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    staging_offerings = _build_staging_offerings(course_plus_offerings)
    offerings = _build_offerings(staging_offerings, semester)
    courses = _build_courses(offerings)
    offering_teachers = _build_offering_teachers(offerings)

    staging_reviews = _build_staging_reviews(community_reviews)
    community_course_variants = _build_community_course_variants(staging_reviews)
    community_variant_teachers = _build_community_variant_teachers(community_course_variants)
    reviews = _build_community_reviews(staging_reviews)

    with sqlite3.connect(db_path) as connection:
        _write_table(connection, "stg_course_plus_offerings", staging_offerings)
        _write_table(connection, "stg_community_reviews", staging_reviews)
        _write_table(connection, "courses", courses)
        _write_table(connection, "offerings", offerings)
        _write_table(connection, "offering_teachers", offering_teachers)
        _write_table(connection, "community_course_variants", community_course_variants)
        _write_table(connection, "community_course_variant_teachers", community_variant_teachers)
        _write_table(connection, "community_reviews", reviews)
        _create_indexes_and_views(connection)


def normalize_teacher_name(value: str) -> str:
    normalized = str(value).strip().translate(TEACHER_SEPARATORS)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def split_teachers(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    normalized = normalize_teacher_name(str(value))
    teachers = []
    for teacher in normalized.split(","):
        teacher = teacher.strip()
        if teacher.lower() not in PLACEHOLDER_TEACHERS:
            teachers.append(teacher)
    return list(dict.fromkeys(teachers))


def _build_staging_offerings(frame: pd.DataFrame) -> pd.DataFrame:
    staging = frame[OFFERING_COLUMNS].copy()
    staging.insert(0, "source_row_id", range(1, len(staging) + 1))
    return staging


def _build_offerings(staging: pd.DataFrame, semester: str) -> pd.DataFrame:
    deduped = staging[OFFERING_COLUMNS].drop_duplicates().reset_index(drop=True)
    offerings = deduped.rename(columns={"teacher": "teacher_raw"})
    offerings.insert(0, "offering_id", range(1, len(offerings) + 1))
    offerings.insert(1, "semester", semester)
    offerings["has_virtual_schedule"] = offerings["has_virtual_schedule"].astype(int)
    offerings["is_weekend_course"] = offerings["is_weekend_course"].astype(int)
    return offerings


def _build_courses(offerings: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        offerings.groupby("course_code", as_index=False)
        .agg(
            course_name=("course_name", _mode_or_first),
            department=("department", _mode_or_first),
            credits=("credits", "median"),
        )
        .sort_values("course_code")
    )
    return grouped


def _build_offering_teachers(offerings: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in offerings.itertuples(index=False):
        for order, teacher in enumerate(split_teachers(row.teacher_raw), start=1):
            rows.append(
                {
                    "offering_id": row.offering_id,
                    "course_code": row.course_code,
                    "teacher_norm": teacher,
                    "teacher_order": order,
                }
            )
    return pd.DataFrame(rows, columns=["offering_id", "course_code", "teacher_norm", "teacher_order"])


def _build_staging_reviews(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[REVIEW_COLUMNS].copy()


def _build_community_course_variants(staging: pd.DataFrame) -> pd.DataFrame:
    variants = (
        staging[["course_id", "course_code", "course_name", "course_teacher"]]
        .drop_duplicates(subset=["course_id"])
        .rename(
            columns={
                "course_id": "community_course_id",
                "course_teacher": "course_teacher_raw",
            }
        )
        .sort_values("community_course_id")
        .reset_index(drop=True)
    )
    return variants


def _build_community_variant_teachers(variants: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in variants.itertuples(index=False):
        for order, teacher in enumerate(split_teachers(row.course_teacher_raw), start=1):
            rows.append(
                {
                    "community_course_id": row.community_course_id,
                    "course_code": row.course_code,
                    "teacher_norm": teacher,
                    "teacher_order": order,
                }
            )
    return pd.DataFrame(
        rows,
        columns=["community_course_id", "course_code", "teacher_norm", "teacher_order"],
    )


def _build_community_reviews(staging: pd.DataFrame) -> pd.DataFrame:
    reviews = staging.rename(
        columns={
            "course_id": "community_course_id",
            "course_teacher": "course_teacher_raw",
        }
    )
    return reviews[
        [
            "review_id",
            "community_course_id",
            "course_code",
            "course_name",
            "course_teacher_raw",
            "rating",
            "score",
            "semester_name",
            "comment",
            "comment_length",
            "created_at",
            "modified_at",
        ]
    ].copy()


def _mode_or_first(values: pd.Series) -> Any:
    mode = values.dropna().mode()
    if not mode.empty:
        return mode.iloc[0]
    return values.iloc[0] if not values.empty else None


def _write_table(connection: sqlite3.Connection, table_name: str, frame: pd.DataFrame) -> None:
    frame.to_sql(table_name, connection, if_exists="replace", index=False)


def _create_indexes_and_views(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    for statement in DROP_VIEWS:
        cursor.execute(statement)
    for statement in CREATE_INDEXES:
        cursor.execute(statement)
    for statement in CREATE_VIEWS:
        cursor.execute(statement)
    connection.commit()
