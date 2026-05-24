from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from pathlib import Path

import pandas as pd

from sjtu_course_analysis.course_plus import (
    DEFAULT_COURSE_PLUS_SEMESTER,
    course_plus_table_name,
    fetch_course_plus_data,
    normalize_course_plus,
    quote_sql_identifier,
)


COURSE_PLUS_COLUMNS = [
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

DEFAULT_INPUT_CSV = Path("data/processed/current_term_2025-2026_1/course_plus_offerings_2025-2026_1_cleaned.csv")
DEFAULT_SQLITE = Path("data/processed/course_reviews_simple.sqlite")
DEFAULT_OUTPUT_ROOT = Path(".")
DEFAULT_SEMESTER = DEFAULT_COURSE_PLUS_SEMESTER
DAY_CODES = {
    "一": "D1",
    "二": "D2",
    "三": "D3",
    "四": "D4",
    "五": "D5",
    "六": "D6",
    "日": "D7",
    "天": "D7",
}
SCHEDULE_PATTERN = re.compile(r"星期([一二三四五六日天])第([^节{}]+)节\{([^{}]+)\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch or ingest Course+ offerings and write them into the simple SQLite database."
    )
    parser.add_argument(
        "--semester",
        help="Course+ semester key, for example 2025-2026_1. Fetches online unless --input-csv is also set.",
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        help=f"Load an already cleaned Course+ CSV. Defaults to {DEFAULT_INPUT_CSV} when --semester is omitted.",
    )
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--timeout", type=int, default=60, help="Course+ request timeout in seconds.")
    parser.add_argument(
        "--save-csv",
        action="store_true",
        help="When fetching from Course+, also save raw and normalized CSV files under data/.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Project root for --save-csv outputs.",
    )
    return parser.parse_args()


def ingest_course_plus(input_csv: Path, sqlite_path: Path, semester: str) -> int:
    offerings = load_offerings(input_csv)
    write_offerings_sqlite(offerings, sqlite_path, semester)
    return len(offerings)


def fetch_and_ingest_course_plus(
    semester: str,
    sqlite_path: Path,
    *,
    timeout: int = 60,
    save_csv: bool = False,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> int:
    raw_frame = fetch_course_plus_data(semester, timeout=timeout)
    normalized_frame = normalize_course_plus(raw_frame)

    if save_csv:
        save_course_plus_csvs(raw_frame, normalized_frame, semester, output_root)

    offerings = frame_to_offerings(normalized_frame)
    write_offerings_sqlite(offerings, sqlite_path, semester)
    return len(offerings)


def write_offerings_sqlite(offerings: list[dict[str, object]], sqlite_path: Path, semester: str) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    table_name = course_plus_table_name(semester)
    quoted_table_name = quote_sql_identifier(table_name)

    with sqlite3.connect(sqlite_path) as connection:
        create_table(connection, table_name)
        connection.execute(f"DELETE FROM {quoted_table_name}")
        connection.executemany(
            f"""
            INSERT INTO {quoted_table_name} (
                course_code, course_name, department, teacher, credits, enrollment,
                class_capacity, teaching_class, campus, course_type, schedule_text,
                schedule_code, has_virtual_schedule, is_weekend_course, time_bucket
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ([offering[column] for column in COURSE_PLUS_COLUMNS_WITH_CODE] for offering in offerings),
        )
        create_indexes(connection, table_name)


def frame_to_offerings(frame: pd.DataFrame) -> list[dict[str, object]]:
    missing_columns = [column for column in COURSE_PLUS_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

    offerings: list[dict[str, object]] = []
    for row in frame[COURSE_PLUS_COLUMNS].to_dict(orient="records"):
        offering = {column: clean_text(row[column]) for column in COURSE_PLUS_COLUMNS}
        offering["credits"] = parse_float(offering["credits"])
        offering["enrollment"] = parse_int(offering["enrollment"])
        offering["class_capacity"] = parse_int_or_float(offering["class_capacity"])
        offering["schedule_code"] = encode_schedule(str(offering["schedule_text"]))
        offerings.append(offering)
    return offerings


def load_offerings(input_csv: Path) -> list[dict[str, object]]:
    offerings: list[dict[str, object]] = []

    with input_csv.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")
        missing_columns = [column for column in COURSE_PLUS_COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

        for row in reader:
            offering = {column: clean_text(row[column]) for column in COURSE_PLUS_COLUMNS}
            offering["credits"] = parse_float(offering["credits"])
            offering["enrollment"] = parse_int(offering["enrollment"])
            offering["class_capacity"] = parse_int_or_float(offering["class_capacity"])
            offering["schedule_code"] = encode_schedule(str(offering["schedule_text"]))
            offerings.append(offering)

    return offerings


def save_course_plus_csvs(
    raw_frame: pd.DataFrame,
    normalized_frame: pd.DataFrame,
    semester: str,
    output_root: Path,
) -> None:
    raw_dir = output_root / "data" / "raw" / f"current_term_{semester}"
    processed_dir = output_root / "data" / "processed" / f"current_term_{semester}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    raw_frame.to_csv(raw_dir / f"course_plus_raw_{semester}.csv", index=False)
    normalized_frame.to_csv(processed_dir / f"course_plus_offerings_{semester}.csv", index=False)


def encode_schedule(schedule_text: str) -> str:
    entries: list[str] = []
    for part in filter(None, (item.strip() for item in schedule_text.split(";"))):
        match = SCHEDULE_PATTERN.search(part)
        if not match:
            entries.append(f"RAW:{part}")
            continue
        day, periods_text, weeks_text = match.groups()
        day_code = DAY_CODES[day]
        periods = "+".join(f"P{period.strip()}" for period in periods_text.split(",") if period.strip())
        weeks = "+".join(f"W{week.strip().removesuffix('周')}" for week in weeks_text.split(",") if week.strip())
        entries.append(f"{day_code}:{periods}:{weeks}")
    return ";".join(entries)


def create_table(connection: sqlite3.Connection, table_name: str) -> None:
    quoted_table_name = quote_sql_identifier(table_name)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quoted_table_name} (
            course_code TEXT,
            course_name TEXT,
            department TEXT,
            teacher TEXT,
            credits REAL,
            enrollment INTEGER,
            class_capacity REAL,
            teaching_class TEXT,
            campus TEXT,
            course_type TEXT,
            schedule_text TEXT,
            schedule_code TEXT,
            has_virtual_schedule TEXT,
            is_weekend_course TEXT,
            time_bucket TEXT
        )
        """
    )


def create_indexes(connection: sqlite3.Connection, table_name: str) -> None:
    quoted_table_name = quote_sql_identifier(table_name)
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_course_code ON {quoted_table_name}(course_code)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_teaching_class ON {quoted_table_name}(teaching_class)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_schedule_code ON {quoted_table_name}(schedule_code)"
    )


def resolve_semester(args: argparse.Namespace) -> str:
    return args.semester or DEFAULT_SEMESTER


def resolve_input_csv(args: argparse.Namespace) -> Path:
    return args.input_csv or DEFAULT_INPUT_CSV


def should_fetch_course_plus(args: argparse.Namespace) -> bool:
    return args.semester is not None and args.input_csv is None


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_float(value: object) -> float | None:
    text = clean_text(value)
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: object) -> int | None:
    number = parse_float(value)
    if number is None:
        return None
    return int(number)


def parse_int_or_float(value: object) -> int | float | None:
    number = parse_float(value)
    if number is None:
        return None
    if number.is_integer():
        return int(number)
    return number


def main() -> None:
    args = parse_args()
    semester = resolve_semester(args)
    if should_fetch_course_plus(args):
        row_count = fetch_and_ingest_course_plus(
            semester,
            args.sqlite,
            timeout=args.timeout,
            save_csv=args.save_csv,
            output_root=args.output_root,
        )
        print(f"Fetched Course+ semester: {semester}")
    else:
        input_csv = resolve_input_csv(args)
        row_count = ingest_course_plus(input_csv, args.sqlite, semester)
        print(f"Loaded Course+ CSV: {input_csv}")

    print(f"Refreshed Course+ table: {course_plus_table_name(semester)}")
    print(f"Rows written: {row_count}")
    print(f"SQLite database updated: {args.sqlite}")


COURSE_PLUS_COLUMNS_WITH_CODE = [
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
    "schedule_code",
    "has_virtual_schedule",
    "is_weekend_course",
    "time_bucket",
]


if __name__ == "__main__":
    main()
