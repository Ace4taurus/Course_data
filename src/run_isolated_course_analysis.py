from __future__ import annotations

import argparse
from pathlib import Path

from sjtu_course_analysis.course_community import (
    CommunityCredentials,
    CourseCommunityClient,
    aggregate_reviews,
    credentials_from_env,
)
from sjtu_course_analysis.course_plus import aggregate_courses, fetch_course_plus_data, normalize_course_plus
from sjtu_course_analysis.sqlite_export import export_course_lookup_sqlite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build strictly isolated outputs for the current semester and the all-history community review universe."
    )
    parser.add_argument("--semester", default="2025-2026_1", help="Course+ semester key.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("."),
        help="Project root used to store raw and processed data.",
    )
    parser.add_argument(
        "--community-transport",
        choices=["requests", "curl"],
        default="curl",
        help="HTTP transport used for course.sjtu.plus.",
    )
    parser.add_argument("--community-username", help="course.sjtu.plus username or email account.")
    parser.add_argument("--community-password", help="course.sjtu.plus password.")
    parser.add_argument(
        "--community-login-mode",
        choices=["account", "email_password"],
        default="email_password",
        help="Login mode for course.sjtu.plus.",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        help="SQLite database path. Defaults to data/processed/sjtu_course_data.sqlite under output root.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    credentials = None
    if args.community_username and args.community_password:
        credentials = CommunityCredentials(
            username=args.community_username,
            password=args.community_password,
            login_mode=args.community_login_mode,
        )
    else:
        credentials = credentials_from_env()
    if credentials is None:
        raise SystemExit("Missing course.sjtu.plus credentials.")

    output_root = args.output_root
    history_raw_dir = output_root / "data" / "raw" / "history_all_terms"
    history_processed_dir = output_root / "data" / "processed" / "history_all_terms"
    current_raw_dir = output_root / "data" / "raw" / f"current_term_{args.semester}"
    current_processed_dir = output_root / "data" / "processed" / f"current_term_{args.semester}"
    sqlite_path = args.sqlite_path or output_root / "data" / "processed" / "sjtu_course_data.sqlite"

    for directory in [
        history_raw_dir,
        history_processed_dir,
        current_raw_dir,
        current_processed_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    client = CourseCommunityClient(transport=args.community_transport)
    client.login(credentials)
    reviews = client.fetch_reviews()
    history_course_level = aggregate_reviews(reviews)
    history_metrics = history_course_level[
        [
            "course_code",
            "community_avg_rating",
            "community_review_count",
            "community_avg_comment_length",
            "latest_review_at",
        ]
    ].copy()

    reviews.to_csv(history_raw_dir / "course_community_reviews_all_terms.csv", index=False)
    history_course_level.to_csv(history_processed_dir / "course_community_courses_all_terms.csv", index=False)

    raw_course_plus = fetch_course_plus_data(args.semester)
    normalized_course_plus = normalize_course_plus(raw_course_plus)
    current_course_level = aggregate_courses(normalized_course_plus)
    current_merged = current_course_level.merge(history_metrics, on="course_code", how="left")

    raw_course_plus.to_csv(current_raw_dir / f"course_plus_raw_{args.semester}.csv", index=False)
    normalized_course_plus.to_csv(current_processed_dir / f"course_plus_offerings_{args.semester}.csv", index=False)
    current_course_level.to_csv(current_processed_dir / f"course_plus_courses_{args.semester}.csv", index=False)
    history_course_level.to_csv(current_processed_dir / "history_review_aggregate_reference.csv", index=False)
    current_merged.to_csv(current_processed_dir / f"merged_current_courses_with_history_reviews_{args.semester}.csv", index=False)
    export_course_lookup_sqlite(sqlite_path, args.semester, normalized_course_plus, reviews)

    print(f"History raw data saved under: {history_raw_dir}")
    print(f"History processed data saved under: {history_processed_dir}")
    print(f"Current-term processed data saved under: {current_processed_dir}")
    print(f"SQLite database saved to: {sqlite_path}")
    print(f"History reviews fetched: {len(reviews)}")
    print(f"Current-term courses aggregated: {len(current_course_level)}")


if __name__ == "__main__":
    main()
