#!/usr/bin/env python3
"""Fetch Bilibili emote package index and update README."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.bilibili.com/x/emote/setting/panel"
README_START = "<!-- BILIBILI_EMOTE_INDEX_START -->"
README_END = "<!-- BILIBILI_EMOTE_INDEX_END -->"
SHANGHAI_TZ = dt.timezone(dt.timedelta(hours=8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update README with the latest Bilibili emote package index."
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--data", type=Path, default=Path("data/emote_index.json"))
    parser.add_argument("--recent-days", type=int, default=7)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Rebuild generated files from the existing data file without API access.",
    )
    return parser.parse_args()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def cookie_header() -> str | None:
    cookie = os.getenv("BILIBILI_COOKIE")
    if cookie:
        return cookie

    sessdata = os.getenv("BILIBILI_SESSDATA")
    if sessdata:
        return f"SESSDATA={sessdata}"

    return None


def fetch_all_packages(timeout: float) -> list[dict[str, Any]]:
    query = urlencode({"business": "reply"})
    headers = {
        "Accept": "application/json",
        "Referer": "https://www.bilibili.com",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
    }

    cookie = cookie_header()
    if cookie:
        headers["Cookie"] = cookie

    request = Request(f"{API_URL}?{query}", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Bilibili API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to request Bilibili API: {exc.reason}") from exc

    if payload.get("code") != 0:
        message = payload.get("message") or payload.get("msg") or "unknown error"
        raise RuntimeError(f"Bilibili API returned code={payload.get('code')}: {message}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Bilibili API response does not contain a data object")

    packages = data.get("all_packages")
    if not isinstance(packages, list):
        raise RuntimeError("Bilibili API response does not contain all_packages")

    return [package for package in packages if isinstance(package, dict)]


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "packages": []}

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} does not contain a JSON object")

    return payload


def package_id(package: dict[str, Any]) -> int | None:
    try:
        return int(package["id"])
    except (KeyError, TypeError, ValueError):
        return None


def package_mtime(package: dict[str, Any]) -> int:
    try:
        return int(package.get("mtime") or 0)
    except (TypeError, ValueError):
        return 0


def type_label(value: Any) -> str:
    try:
        package_type = int(value)
    except (TypeError, ValueError):
        return "未知"

    labels = {
        1: "普通",
        2: "会员专属",
        3: "购买所得",
        4: "颜文字",
    }
    return labels.get(package_type, f"未知({package_type})")


def format_mtime(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""

    if timestamp <= 0:
        return ""

    return dt.datetime.fromtimestamp(timestamp, tz=SHANGHAI_TZ).strftime(
        "%Y/%m/%d %H:%M"
    )


def package_record(
    package: dict[str, Any],
    previous: dict[str, Any] | None,
    now_iso: str,
) -> dict[str, Any] | None:
    pkg_id = package_id(package)
    name = str(package.get("text") or "").strip()
    preview_url = str(package.get("url") or "").strip()
    if pkg_id is None or not name or not preview_url:
        return None

    first_seen_at = now_iso
    if previous and previous.get("first_seen_at"):
        first_seen_at = str(previous["first_seen_at"])

    mtime = package_mtime(package)
    raw_type = package.get("type")
    return {
        "id": pkg_id,
        "name": name,
        "preview_url": preview_url,
        "mtime": mtime,
        "created_at": format_mtime(mtime),
        "type": raw_type,
        "type_label": type_label(raw_type),
        "first_seen_at": first_seen_at,
    }


def normalize_existing_package(
    package: dict[str, Any], now_iso: str
) -> dict[str, Any] | None:
    pkg_id = package_id(package)
    name = str(package.get("name") or package.get("text") or "").strip()
    preview_url = str(package.get("preview_url") or package.get("url") or "").strip()
    if pkg_id is None or not name or not preview_url:
        return None

    mtime = package_mtime(package)
    raw_type = package.get("type")
    return {
        "id": pkg_id,
        "name": name,
        "preview_url": preview_url,
        "mtime": mtime,
        "created_at": format_mtime(mtime),
        "type": raw_type,
        "type_label": type_label(raw_type),
        "first_seen_at": str(package.get("first_seen_at") or now_iso),
    }


def normalize_index(existing: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    now_iso = now.isoformat().replace("+00:00", "Z")
    records = [
        record
        for package in existing.get("packages", [])
        if isinstance(package, dict)
        for record in [normalize_existing_package(package, now_iso)]
        if record is not None
    ]
    records.sort(key=lambda item: (item["id"], item["name"]))
    return {
        "schema_version": 1,
        "source": existing.get("source") or f"{API_URL}?business=reply",
        "updated_at": str(existing.get("updated_at") or now_iso),
        "count": len(records),
        "packages": records,
    }


def merge_packages(
    fetched: list[dict[str, Any]], existing: dict[str, Any], now: dt.datetime
) -> dict[str, Any]:
    previous_by_id = {
        package["id"]: package
        for package in existing.get("packages", [])
        if isinstance(package, dict) and isinstance(package.get("id"), int)
    }
    now_iso = now.isoformat().replace("+00:00", "Z")

    records: list[dict[str, Any]] = []
    for package in fetched:
        pkg_id = package_id(package)
        previous = previous_by_id.get(pkg_id) if pkg_id is not None else None
        record = package_record(package, previous, now_iso)
        if record is not None:
            records.append(record)

    records.sort(key=lambda item: (item["id"], item["name"]))
    updated_at = now_iso
    if records == existing.get("packages"):
        updated_at = str(existing.get("updated_at") or now_iso)

    return {
        "schema_version": 1,
        "source": f"{API_URL}?business=reply",
        "updated_at": updated_at,
        "count": len(records),
        "packages": records,
    }


def mtime_date(package: dict[str, Any]) -> dt.date | None:
    mtime = package.get("mtime")
    if not isinstance(mtime, int) or mtime <= 0:
        return None

    return dt.datetime.fromtimestamp(mtime, tz=SHANGHAI_TZ).date()


def markdown_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def image_cell(name: str, url: str) -> str:
    escaped_name = html.escape(name, quote=True)
    escaped_url = html.escape(url, quote=True)
    return f'<img src="{escaped_url}" alt="{escaped_name}" width="64">'


def package_row(package: dict[str, Any]) -> str:
    package_id = str(package["id"])
    name = markdown_text(str(package["name"]))
    preview = image_cell(str(package["name"]), str(package["preview_url"]))
    created_at = markdown_text(str(package.get("created_at") or ""))
    type_name = markdown_text(str(package.get("type_label") or type_label(package.get("type"))))
    return f"| `{package_id}` | **{name}** | {preview} | {created_at} | {type_name} |"


def table_for_packages(packages: list[dict[str, Any]]) -> str:
    lines = [
        "| ID | 表情包名称 | 表情包预览 URL | 添加时间 | 表情包类型 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(package_row(package) for package in packages)
    return "\n".join(lines)


def recent_packages(
    packages: list[dict[str, Any]], today: dt.date, recent_days: int
) -> dict[dt.date, list[dict[str, Any]]]:
    earliest = today - dt.timedelta(days=recent_days - 1)
    grouped: dict[dt.date, list[dict[str, Any]]] = {}

    for package in packages:
        date = mtime_date(package)
        if date is None or date < earliest or date > today:
            continue
        grouped.setdefault(date, []).append(package)

    for items in grouped.values():
        items.sort(key=lambda item: (-int(item.get("mtime") or 0), item["id"]))

    return dict(sorted(grouped.items(), reverse=True))


def build_generated_block(index: dict[str, Any], recent_days: int) -> str:
    packages = list(index["packages"])
    today = dt.datetime.now(SHANGHAI_TZ).date()
    recent = recent_packages(packages, today, recent_days)

    lines = [
        README_START,
        "<!-- 下面内容由 scripts/update_emote_index.py 自动生成，请勿手动编辑此区块。 -->",
        "",
        "## **最近 7 天上新表情包**",
        "",
    ]

    if recent:
        for date, items in recent.items():
            lines.extend(
                [
                    f"### **{date.strftime('%Y/%m/%d')}**",
                    "",
                    table_for_packages(items),
                    "",
                ]
            )
    else:
        lines.extend(["暂无最近 7 天上新的表情包。", ""])

    lines.extend(
        [
            "---",
            "",
            "## **全部表情包索引**",
            "",
            (
                f"<details>\n"
                f"<summary>展开全部 {len(packages)} 个表情包预览 URL</summary>\n"
            ),
            table_for_packages(packages),
            "",
            "</details>",
            "",
            README_END,
        ]
    )
    return "\n".join(lines)


def ensure_bold_title(readme: str) -> str:
    lines = readme.splitlines()
    if not lines:
        return "# **bilibili-emote**\n"

    if lines[0].strip() == "# bilibili-emote":
        lines[0] = "# **bilibili-emote**"

    return "\n".join(lines).rstrip() + "\n"


def update_readme(readme_path: Path, generated_block: str) -> None:
    existing = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    existing = ensure_bold_title(existing)

    start = existing.find(README_START)
    end = existing.find(README_END)
    if start != -1 and end != -1 and start < end:
        end += len(README_END)
        updated = existing[:start].rstrip() + "\n\n" + generated_block + existing[end:]
    else:
        updated = existing.rstrip() + "\n\n---\n\n" + generated_block + "\n"

    readme_path.write_text(updated.rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    now = utc_now()

    existing = load_existing(args.data)
    if args.offline:
        index = normalize_index(existing, now)
    else:
        fetched = fetch_all_packages(args.timeout)
        index = merge_packages(fetched, existing, now)

    args.data.parent.mkdir(parents=True, exist_ok=True)
    args.data.write_text(
        json.dumps(index, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    update_readme(args.readme, build_generated_block(index, args.recent_days))

    print(f"Updated {args.data} and {args.readme} with {index['count']} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
