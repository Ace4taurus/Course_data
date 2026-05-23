from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests


BASE_URL = "https://course.sjtu.plus"


class CommunityAuthError(RuntimeError):
    """Raised when course.sjtu.plus authentication fails."""


@dataclass
class CommunityCredentials:
    username: str
    password: str
    login_mode: str = "account"


class CourseCommunityClient:
    LOGIN_PATHS = {
        "account": ("/oauth/login/", lambda creds: {"username": creds.username, "password": creds.password}),
        "email_password": ("/oauth/email/login/", lambda creds: {"account": creds.username, "password": creds.password}),
    }

    def __init__(self, base_url: str = BASE_URL, timeout: int = 30, transport: str = "requests") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def login(self, credentials: CommunityCredentials) -> dict[str, Any]:
        if credentials.login_mode not in self.LOGIN_PATHS:
            raise ValueError(f"Unsupported login mode: {credentials.login_mode}")
        if self.transport == "curl":
            return self._curl_login(credentials)
        return self._requests_login(credentials)

    def fetch_reviews(self, page_size: int = 100) -> pd.DataFrame:
        payload = self._fetch_paginated("/api/review/", page_size)
        rows = []
        for item in payload:
            course = item.get("course") or {}
            semester = item.get("semester")
            if isinstance(semester, dict):
                semester_name = semester.get("name")
            else:
                semester_name = semester
            comment = item.get("comment") or ""
            rows.append(
                {
                    "review_id": item.get("id"),
                    "course_id": course.get("id"),
                    "course_code": course.get("code"),
                    "course_name": course.get("name"),
                    "course_teacher": course.get("teacher"),
                    "rating": item.get("rating"),
                    "score": item.get("score"),
                    "semester_name": semester_name,
                    "comment": comment,
                    "comment_length": len(comment),
                    "created_at": item.get("created_at"),
                    "modified_at": item.get("modified_at"),
                }
            )
        frame = pd.DataFrame(rows)
        if not frame.empty:
            frame["course_code"] = frame["course_code"].astype(str)
            frame["rating"] = pd.to_numeric(frame["rating"], errors="coerce")
            frame["comment_length"] = pd.to_numeric(frame["comment_length"], errors="coerce")
        return frame

    def _fetch_paginated(self, path: str, page_size: int) -> list[dict[str, Any]]:
        if self.transport == "curl":
            return self._curl_fetch_paginated(path, page_size)
        return self._requests_fetch_paginated(path, page_size)

    def _requests_login(self, credentials: CommunityCredentials) -> dict[str, Any]:
        self.session.get(self.base_url + "/login", timeout=self.timeout)
        path, payload_builder = self.LOGIN_PATHS[credentials.login_mode]
        response = self.session.post(
            self.base_url + path,
            json=payload_builder(credentials),
            timeout=self.timeout,
        )
        data = _decode_json_response(response.text)
        if response.status_code >= 400:
            detail = data.get("detail") or data.get("error") or response.text
            raise CommunityAuthError(str(detail))
        return data

    def _requests_fetch_paginated(self, path: str, page_size: int) -> list[dict[str, Any]]:
        page = 1
        results: list[dict[str, Any]] = []
        while True:
            response = self.session.get(
                self.base_url + f"{path}?page={page}&size={page_size}",
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            page_results = payload.get("results", [])
            results.extend(page_results)
            if not page_results or len(results) >= payload.get("count", 0):
                break
            page += 1
        return results

    def _curl_login(self, credentials: CommunityCredentials) -> dict[str, Any]:
        path, payload_builder = self.LOGIN_PATHS[credentials.login_mode]
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_path = Path(tmpdir) / "course_session.cookies"
            self._curl(
                [
                    "-c",
                    str(cookie_path),
                    "-b",
                    str(cookie_path),
                    f"{self.base_url}/login",
                ]
            )
            body = self._curl(
                [
                    "-c",
                    str(cookie_path),
                    "-b",
                    str(cookie_path),
                    "-H",
                    "Content-Type: application/json",
                    "-X",
                    "POST",
                    f"{self.base_url}{path}",
                    "--data",
                    json.dumps(payload_builder(credentials)),
                ]
            )
            data = _decode_json_response(body)
            if data.get("detail"):
                raise CommunityAuthError(str(data["detail"]))
            self._cookie_cache = cookie_path.read_text(encoding="utf-8")
            return data

    def _curl_fetch_paginated(self, path: str, page_size: int) -> list[dict[str, Any]]:
        if not hasattr(self, "_cookie_cache"):
            raise CommunityAuthError("Curl transport requires login before fetching data.")
        page = 1
        results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_path = Path(tmpdir) / "course_session.cookies"
            cookie_path.write_text(self._cookie_cache, encoding="utf-8")
            while True:
                body = self._curl(
                    [
                        "-c",
                        str(cookie_path),
                        "-b",
                        str(cookie_path),
                        f"{self.base_url}{path}?page={page}&size={page_size}",
                    ]
                )
                payload = _decode_json_response(body)
                page_results = payload.get("results", [])
                results.extend(page_results)
                if not page_results or len(results) >= payload.get("count", 0):
                    break
                page += 1
        return results

    def _curl(self, extra_args: list[str]) -> str:
        command = ["curl", "-sS", "-L", *extra_args]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return result.stdout


def aggregate_reviews(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "course_code",
                "course_name",
                "course_teacher",
                "community_avg_rating",
                "community_review_count",
                "community_avg_comment_length",
                "latest_review_at",
            ]
        )
    grouped = (
        frame.groupby("course_code", as_index=False)
        .agg(
            course_name=("course_name", "first"),
            course_teacher=("course_teacher", "first"),
            community_avg_rating=("rating", "mean"),
            community_review_count=("review_id", "count"),
            community_avg_comment_length=("comment_length", "mean"),
            latest_review_at=("modified_at", "max"),
        )
        .sort_values("community_review_count", ascending=False)
    )
    grouped["community_avg_rating"] = grouped["community_avg_rating"].round(3)
    grouped["community_avg_comment_length"] = grouped["community_avg_comment_length"].round(1)
    return grouped


def load_demo_reviews(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["course_code"] = frame["course_code"].astype(str)
    return frame.rename(
        columns={
            "avg_rating": "community_avg_rating",
            "review_count": "community_review_count",
        }
    )


def credentials_from_env() -> CommunityCredentials | None:
    username = os.getenv("COURSE_COMMUNITY_USERNAME")
    password = os.getenv("COURSE_COMMUNITY_PASSWORD")
    if not username or not password:
        return None
    login_mode = os.getenv("COURSE_COMMUNITY_LOGIN_MODE", "account")
    return CommunityCredentials(username=username, password=password, login_mode=login_mode)


def _decode_json_response(text: str) -> dict[str, Any]:
    return json.loads(text) if text.strip() else {}
