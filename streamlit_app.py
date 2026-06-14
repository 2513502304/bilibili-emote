from __future__ import annotations

import datetime as dt
import html
import io
import json
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "data" / "emote_index.json"
DETAIL_API = "https://api.bilibili.com/x/emote/package"
REFERER = "https://www.bilibili.com"
APP_TIMEZONE = dt.timezone(dt.timedelta(hours=8), "UTC+8")
PAGE_SIZE_OPTIONS = [24, 48, 96, 144]
DEFAULT_PAGE_SIZE = 48
SELECTED_IDS_KEY = "selected_package_ids"
PAGE_KEY = "result_page"
PENDING_PAGE_KEY = "pending_result_page"
RESULTS_TOP_ID = "emote-results-top"


st.set_page_config(
    page_title="Bilibili emote downloader",
    page_icon=":material/download:",
    layout="wide",
)


st.markdown(
    """
    <style>
    :root {
        --be-ink: #111827;
        --be-muted: #64748b;
        --be-border: rgba(15, 23, 42, 0.11);
        --be-soft: #f8fafc;
        --be-pink: #fb7299;
        --be-teal: #00a1d6;
        --be-green: #059669;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2.25rem;
    }

    [data-testid="stMetric"] {
        background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
        border: 1px solid var(--be-border);
        border-radius: 8px;
        padding: 0.9rem 1rem 0.75rem;
    }

    [data-testid="stTextInput"] [data-testid="InputInstructions"],
    [data-testid="stNumberInput"] [data-testid="InputInstructions"] {
        display: none;
    }

    .app-kicker {
        color: var(--be-teal);
        font-size: 0.78rem;
        font-weight: 760;
        letter-spacing: 0;
        margin-bottom: 0.35rem;
    }

    .app-title {
        color: var(--be-ink);
        font-size: 2.1rem;
        font-weight: 780;
        line-height: 1.12;
        margin: 0;
    }

    .app-subtitle {
        color: var(--be-muted);
        font-size: 0.98rem;
        line-height: 1.6;
        margin-top: 0.55rem;
        max-width: 820px;
    }

    .toolbar-note {
        color: var(--be-muted);
        font-size: 0.84rem;
        margin-top: 0.25rem;
    }

    .emote-card {
        border: 1px solid var(--be-border);
        border-radius: 8px;
        background: #ffffff;
        min-height: 232px;
        padding: 0.8rem;
    }

    .emote-cover {
        align-items: center;
        background:
            linear-gradient(135deg, rgba(0, 161, 214, 0.08), rgba(251, 114, 153, 0.10)),
            #f8fafc;
        border: 1px solid rgba(15, 23, 42, 0.07);
        border-radius: 8px;
        display: flex;
        height: 96px;
        justify-content: center;
        margin-bottom: 0.65rem;
        overflow: hidden;
        width: 100%;
    }

    .emote-cover img {
        max-height: 82px;
        max-width: 82px;
        object-fit: contain;
    }

    .emote-name {
        color: var(--be-ink);
        font-size: 0.95rem;
        font-weight: 730;
        line-height: 1.35;
        margin-bottom: 0.25rem;
        min-height: 2.55rem;
        word-break: break-word;
    }

    .emote-meta {
        color: var(--be-muted);
        font-size: 0.78rem;
        line-height: 1.45;
    }

    .pill-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        margin-top: 0.45rem;
    }

    .pill {
        border-radius: 999px;
        border: 1px solid #dbeafe;
        background: #eff6ff;
        color: #1d4ed8;
        display: inline-flex;
        font-size: 0.72rem;
        font-weight: 700;
        padding: 0.12rem 0.46rem;
        white-space: nowrap;
    }

    .pill-pink {
        background: #fff1f5;
        border-color: #ffd6e3;
        color: #be185d;
    }

    .pill-green {
        background: #ecfdf5;
        border-color: #bbf7d0;
        color: #047857;
    }

    .download-panel {
        border: 1px solid var(--be-border);
        border-radius: 8px;
        background: #ffffff;
        padding: 1rem;
    }

    .selection-line {
        color: var(--be-muted);
        font-size: 0.86rem;
        line-height: 1.5;
    }

    .section-anchor {
        height: 1px;
        overflow: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def load_index(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)

    packages = payload.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("data/emote_index.json does not contain a package list")

    return payload


def init_state() -> None:
    st.session_state.setdefault(SELECTED_IDS_KEY, set())
    st.session_state.setdefault(PAGE_KEY, 1)


def normalize_filepath(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip()
    value = re.sub(r"\s+", " ", value)
    value = value.rstrip(". ")
    return value or "untitled"


def type_badge(package: dict[str, Any]) -> str:
    return str(package.get("type_label") or "未知")


def searchable_text(package: dict[str, Any]) -> str:
    return f"{package.get('id', '')} {package.get('name', '')}".casefold()


def filter_packages(packages: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return packages

    return [
        package
        for package in packages
        if all(term in searchable_text(package) for term in terms)
    ]


def page_count(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def apply_pending_page(max_page: int) -> None:
    pending = st.session_state.pop(PENDING_PAGE_KEY, None)
    if pending is not None:
        st.session_state[PAGE_KEY] = min(max(1, int(pending)), max_page)
    else:
        st.session_state[PAGE_KEY] = min(max(1, int(st.session_state[PAGE_KEY])), max_page)


def request_page(page: int) -> None:
    st.session_state[PENDING_PAGE_KEY] = page
    st.rerun()


def package_by_id(packages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(package["id"]): package for package in packages}


def render_header(index: dict[str, Any], packages: list[dict[str, Any]]) -> None:
    st.markdown(
        """
        <div class="app-kicker">BILIBILI EMOTE ARCHIVE</div>
        <h1 class="app-title">Bilibili 表情包下载器</h1>
        <div class="app-subtitle">
        搜索每天更新的表情包索引，选择一个或多个表情包，在浏览器中生成和下载与本地全量爬取一致的目录结构。
        </div>
        """,
        unsafe_allow_html=True,
    )

    updated_at = str(index.get("updated_at") or "")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("索引总数", f"{len(packages):,}")
    col_b.metric("已选择", f"{len(st.session_state[SELECTED_IDS_KEY]):,}")
    col_c.metric("索引更新时间", updated_at.replace("T", " ").replace("Z", " UTC"))


def render_package_card(package: dict[str, Any], selected_ids: set[int]) -> None:
    package_id = int(package["id"])
    safe_name = html.escape(str(package["name"]))
    safe_url = html.escape(str(package["preview_url"]), quote=True)
    checked = package_id in selected_ids

    st.markdown(
        f"""
        <div class="emote-card">
            <div class="emote-cover">
                <img src="{safe_url}" alt="{safe_name}">
            </div>
            <div class="emote-name">{safe_name}</div>
            <div class="emote-meta">ID: {package_id}</div>
            <div class="emote-meta">添加时间: {html.escape(str(package.get("created_at") or ""))}</div>
            <div class="pill-row">
                <span class="pill">{html.escape(type_badge(package))}</span>
                <span class="pill pill-pink">预览图</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    next_value = st.checkbox(
        "选择",
        value=checked,
        key=f"select_{package_id}",
        label_visibility="collapsed",
    )
    if next_value:
        selected_ids.add(package_id)
    else:
        selected_ids.discard(package_id)


def render_grid(packages: list[dict[str, Any]], page: int, page_size: int) -> None:
    selected_ids = st.session_state[SELECTED_IDS_KEY]
    start = (page - 1) * page_size
    page_items = packages[start : start + page_size]

    st.markdown(f'<div id="{RESULTS_TOP_ID}" class="section-anchor"></div>', unsafe_allow_html=True)
    if not page_items:
        st.info("没有匹配的表情包。")
        return

    for row_start in range(0, len(page_items), 4):
        columns = st.columns(4)
        for column, package in zip(columns, page_items[row_start : row_start + 4]):
            with column:
                render_package_card(package, selected_ids)


def render_pagination(current_page: int, max_page: int, key_prefix: str) -> None:
    first, previous, page_info, next_, last = st.columns([1, 1, 2, 1, 1])
    with first:
        if st.button(
            "首页",
            disabled=current_page <= 1,
            key=f"{key_prefix}_first",
            use_container_width=True,
        ):
            request_page(1)
    with previous:
        if st.button(
            "上一页",
            disabled=current_page <= 1,
            key=f"{key_prefix}_previous",
            use_container_width=True,
        ):
            request_page(current_page - 1)
    with page_info:
        st.markdown(
            f"<div class='toolbar-note' style='text-align:center;'>第 {current_page} / {max_page} 页</div>",
            unsafe_allow_html=True,
        )
    with next_:
        if st.button(
            "下一页",
            disabled=current_page >= max_page,
            key=f"{key_prefix}_next",
            use_container_width=True,
        ):
            request_page(current_page + 1)
    with last:
        if st.button(
            "末页",
            disabled=current_page >= max_page,
            key=f"{key_prefix}_last",
            use_container_width=True,
        ):
            request_page(max_page)


def request_json(url: str, timeout: float = 30.0) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Referer": REFERER,
            "User-Agent": "Mozilla/5.0 BilibiliEmoteDownloader/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc


def request_bytes(url: str, timeout: float = 45.0) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "*/*",
            "Referer": REFERER,
            "User-Agent": "Mozilla/5.0 BilibiliEmoteDownloader/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc


def fetch_package_detail(package_id: int) -> dict[str, Any]:
    payload = request_json(f"{DETAIL_API}?business=reply&ids={package_id}")
    if payload.get("code") != 0:
        message = payload.get("message") or payload.get("msg") or "unknown error"
        raise RuntimeError(f"Package {package_id} failed: {message}")

    packages = payload.get("data", {}).get("packages", [])
    if not packages:
        raise RuntimeError(f"Package {package_id} returned no detail")

    return packages[0]


def extension_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    return ".png"


def build_zip(selected_packages: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for package in selected_packages:
            package_id = int(package["id"])
            package_name = str(package["name"])
            safe_package_name = normalize_filepath(package_name)
            detail = fetch_package_detail(package_id)
            package_dir = f"bilibili-emote/images/{package_id}_{safe_package_name}"
            json_path = f"bilibili-emote/jsons/{package_id}_{safe_package_name}.json"
            archive.writestr(
                json_path,
                json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
            )

            for emote in detail.get("emote", []) or []:
                if not isinstance(emote, dict):
                    continue

                emote_name = normalize_filepath(str(emote.get("text") or emote.get("id") or "emote"))
                emote_url = str(emote.get("url") or "")
                if not emote_url.startswith(("http://", "https://")):
                    continue

                emote_path = f"{package_dir}/{emote_name}{extension_from_url(emote_url)}"
                archive.writestr(emote_path, request_bytes(emote_url))

    return buffer.getvalue()


def selected_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = package_by_id(packages)
    selected_ids = sorted(st.session_state[SELECTED_IDS_KEY])
    return [by_id[package_id] for package_id in selected_ids if package_id in by_id]


def render_download_panel(packages: list[dict[str, Any]]) -> None:
    st.markdown("<div class='download-panel'>", unsafe_allow_html=True)
    current_selection = selected_packages(packages)
    selected_count = len(current_selection)
    st.markdown(f"**已选择 {selected_count} 个表情包**")

    if selected_count:
        names = "、".join(str(package["name"]) for package in current_selection[:8])
        if selected_count > 8:
            names += f" 等 {selected_count} 个"
        st.markdown(f"<div class='selection-line'>{html.escape(names)}</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='selection-line'>从搜索结果或全量索引中勾选表情包后即可下载。</div>", unsafe_allow_html=True)

    panel_cols = st.columns([1, 1, 2])
    with panel_cols[0]:
        if st.button("清空选择", disabled=selected_count == 0, use_container_width=True):
            st.session_state[SELECTED_IDS_KEY].clear()
            st.rerun()

    with panel_cols[1]:
        prepare = st.button("生成压缩包", disabled=selected_count == 0, use_container_width=True)

    if prepare and current_selection:
        with st.spinner("正在获取表情包明细并打包..."):
            try:
                archive = build_zip(current_selection)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                file_name = f"bilibili-emote-{dt.datetime.now(APP_TIMEZONE):%Y%m%d-%H%M%S}.zip"
                st.download_button(
                    "下载压缩包",
                    data=archive,
                    file_name=file_name,
                    mime="application/zip",
                    use_container_width=True,
                )

    st.markdown("</div>", unsafe_allow_html=True)


def main() -> None:
    init_state()
    index = load_index(str(INDEX_PATH))
    packages = list(index["packages"])

    render_header(index, packages)
    st.divider()

    left, right = st.columns([3, 1])
    with left:
        query = st.text_input(
            "搜索 ID 或表情包名称",
            placeholder="例如：1 小黄脸 / 9888 小狐 / 2233",
        )
    with right:
        page_size = st.selectbox(
            "每页数量",
            PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(DEFAULT_PAGE_SIZE),
        )

    filtered = filter_packages(packages, query)
    max_page = page_count(len(filtered), page_size)
    if query and st.session_state.get("_last_query") != query:
        st.session_state[PAGE_KEY] = 1
    st.session_state["_last_query"] = query
    apply_pending_page(max_page)
    current_page = st.session_state[PAGE_KEY]

    st.markdown(
        f"<div class='toolbar-note'>当前显示 {len(filtered):,} / {len(packages):,} 个表情包。</div>",
        unsafe_allow_html=True,
    )

    render_download_panel(packages)
    st.divider()
    render_pagination(current_page, max_page, "top")
    render_grid(filtered, current_page, page_size)
    render_pagination(current_page, max_page, "bottom")


if __name__ == "__main__":
    main()
