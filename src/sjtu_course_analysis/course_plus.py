from __future__ import annotations

import re
from typing import Any

import pandas as pd
import requests


COURSE_PLUS_URL = "https://geek.sjtu.edu.cn/course-plus-data/lessonData_{semester}.json"
COURSE_PLUS_TABLE_PREFIX = "course_plus_offerings"
DEFAULT_COURSE_PLUS_SEMESTER = "2025-2026_1"


def course_plus_table_name(semester: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", semester).strip("_")
    if not normalized:
        raise ValueError("Semester cannot be empty.")
    return f"{COURSE_PLUS_TABLE_PREFIX}_{normalized}"


def quote_sql_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][0-9A-Za-z_]*", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f'"{identifier}"'


def fetch_course_plus_data(semester: str, timeout: int = 60) -> pd.DataFrame:
    response = requests.get(COURSE_PLUS_URL.format(semester=semester), timeout=timeout)
    response.raise_for_status()
    frame = pd.DataFrame(response.json())
    if frame.empty:
        raise ValueError(f"No Course+ data returned for semester {semester}")
    return frame


def normalize_course_plus(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    working["raw_teaching_class"] = working["jxbmc"].astype(str)
    working["course_code"] = working["kch"].astype(str)
    working["course_name"] = working["kcmc"].astype(str)
    working["department"] = working["kkxy"].astype(str)
    working["teacher"] = working["zjs"].fillna(working["jszc"]).astype(str)
    working["credits"] = pd.to_numeric(working["xf"], errors="coerce")
    working["enrollment"] = pd.to_numeric(working["xkrs"], errors="coerce").fillna(0)
    working["class_capacity"] = pd.to_numeric(working["jxbrs"], errors="coerce")
    working["teaching_class"] = working["raw_teaching_class"].str[:-3]
    working["campus"] = working["xqmc"].astype(str)
    working["course_type"] = working["kcxzmc"].astype(str)
    working["schedule_text"] = working["sksj"].astype(str)
    working["has_virtual_schedule"] = working.get("xkbz", "").fillna("").astype(str).str.contains("虚拟排课")
    working["is_weekend_course"] = working["schedule_text"].str.contains("星期六|星期日")
    working["time_bucket"] = working["schedule_text"].map(_infer_time_bucket)
    working = working.drop_duplicates(
        subset=[
            "course_code",
            "course_name",
            "department",
            "teacher",
            "credits",
            "enrollment",
            "class_capacity",
            "raw_teaching_class",
            "campus",
            "course_type",
            "schedule_text",
            "has_virtual_schedule",
            "is_weekend_course",
            "time_bucket",
        ]
    )
    working = (
        working.groupby(["course_code", "teacher", "teaching_class", "schedule_text"], as_index=False)
        .agg(
            course_name=("course_name", "first"),
            department=("department", _mode_or_first),
            credits=("credits", "median"),
            enrollment=("enrollment", "sum"),
            class_capacity=("class_capacity", "sum"),
            campus=("campus", _mode_or_first),
            course_type=("course_type", _mode_or_first),
            has_virtual_schedule=("has_virtual_schedule", "max"),
            is_weekend_course=("is_weekend_course", "max"),
            time_bucket=("time_bucket", "first"),
        )
        .sort_values(["department", "course_code", "teacher", "teaching_class", "schedule_text"])
    )
    return working[
        [
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
    ]


def aggregate_courses(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        frame.groupby("course_code", as_index=False)
        .agg(
            course_name=("course_name", "first"),
            department=("department", _mode_or_first),
            credits=("credits", "median"),
            offering_count=("teaching_class", "count"),
            teacher_count=("teacher", "nunique"),
            total_enrollment=("enrollment", "sum"),
            avg_class_capacity=("class_capacity", "mean"),
            campus_count=("campus", "nunique"),
            primary_course_type=("course_type", _mode_or_first),
            dominant_time_bucket=("time_bucket", _mode_or_first),
            weekend_offering_ratio=("is_weekend_course", "mean"),
            virtual_schedule_ratio=("has_virtual_schedule", "mean"),
        )
        .sort_values(["department", "course_code"])
    )
    grouped["avg_class_capacity"] = grouped["avg_class_capacity"].round(2)
    grouped["weekend_offering_ratio"] = grouped["weekend_offering_ratio"].round(3)
    grouped["virtual_schedule_ratio"] = grouped["virtual_schedule_ratio"].round(3)
    return grouped


def _mode_or_first(values: pd.Series) -> Any:
    mode = values.mode()
    if not mode.empty:
        return mode.iloc[0]
    return values.iloc[0]


def _infer_time_bucket(schedule_text: str) -> str:
    if "第11" in schedule_text or "第12" in schedule_text or "第13" in schedule_text or "第14" in schedule_text:
        return "evening"
    if "第1" in schedule_text or "第2" in schedule_text or "第3" in schedule_text or "第4" in schedule_text:
        return "morning"
    if "第5" in schedule_text or "第6" in schedule_text or "第7" in schedule_text or "第8" in schedule_text:
        return "afternoon"
    return "mixed"
