from __future__ import annotations

import asyncio
import datetime as dt
import html
import io
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse
from zipfile import ZIP_DEFLATED, ZipFile

import orjson
import pandas as pd
import streamlit as st
from waifuboard import Booru
from waifuboard.utils import normalize_filepath


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "data" / "emote_index.json"
DETAIL_API = "https://api.bilibili.com/x/emote/package"
REFERER = "https://www.bilibili.com"
APP_TIMEZONE = dt.timezone(dt.timedelta(hours=8), "UTC+8")
PAGE_SIZE_OPTIONS = [24, 48, 96, 144]
DEFAULT_PAGE_SIZE = 48
MEDIA_CONCURRENCY = 32
DETAIL_PROGRESS_WEIGHT = 0.35
TRUSTED_IMAGE_HOST_SUFFIXES = ("hdslb.com", "bilibili.com")
SELECTED_IDS_KEY = "selected_package_ids"
ARCHIVE_BYTES_KEY = "archive_bytes"
ARCHIVE_NAME_KEY = "archive_name"
ARCHIVE_SELECTION_KEY = "archive_selection"
ARCHIVE_REPORT_KEY = "archive_report"
BATCH_SELECT_KEY = "batch_select_ids"
PAGE_KEY = "result_page"
PENDING_PAGE_KEY = "pending_result_page"
RESULTS_TOP_ID = "emote-results-top"
SEARCH_HELP = """- 支持按表情包 ID 或名称搜索，多个关键词会同时匹配。
- 示例：`1 小黄脸` 会优先定位名称和 ID 都包含对应关键词的结果。
- 索引来自每天自动更新的数据文件，搜索不会请求 Bilibili 接口。"""
PAGE_SIZE_HELP = """- 控制当前页面一次渲染多少个表情包卡片。
- 数量越大，页面预览图越多，首次渲染和滚动会更重。
- 示例：浏览全量索引时可用 `48`，快速翻页时可用 `96`。"""
PAGE_JUMP_HELP = """- 输入页码后会直接跳转到对应页面。
- 左右两侧的 `-` / `+` 按钮用于快速切换上一页或下一页。
- 搜索条件变化时会回到第 1 页，避免停留在旧结果的页码上。"""
BATCH_SELECT_HELP = """- 从当前搜索结果中批量添加或移除表情包。
- 这里的选择会和下方卡片 checkbox 保持同步。
- 示例：先搜索 `充电`，再在这里多选需要下载的充电表情包。"""
PAGE_SELECT_HELP = """- 勾选后选择当前页显示的所有表情包。
- 取消勾选后只取消当前页，不影响其他分页上已选择的表情包。
- 切换搜索条件或分页时，此 checkbox 会反映当前页是否已全部选中。"""
PREPARE_ARCHIVE_HELP = """- 只有点击这个按钮后，才会开始获取表情包明细、下载公开图片并写入 zip。
- 打包发生在运行 Streamlit 的服务端；本机运行时是本机，远程部署时是远程服务器。
- 选择变化后，已经生成的 zip 会失效，需要重新生成。"""
SAVE_ARCHIVE_HELP = """- 只有成功生成 zip 后才可点击。
- 点击后浏览器保存已经生成好的压缩包，不会再次请求 Bilibili 接口。
- 如果新增或取消选择，此按钮会重新变为不可点击，直到再次生成压缩包。"""


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
        background: transparent;
        border: 0;
        padding: 0;
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

    .stat-strip {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 1rem;
    }

    .stat-chip {
        align-items: center;
        background: #f8fafc;
        border: 1px solid var(--be-border);
        border-radius: 8px;
        color: var(--be-ink);
        display: inline-flex;
        font-size: 0.82rem;
        gap: 0.38rem;
        line-height: 1;
        padding: 0.42rem 0.58rem;
        white-space: nowrap;
    }

    .stat-chip span {
        color: var(--be-muted);
        font-size: 0.76rem;
    }

    .section-heading {
        align-items: center;
        color: var(--be-ink);
        display: flex;
        font-size: 1rem;
        font-weight: 760;
        gap: 0.45rem;
        margin: 0.35rem 0 0.6rem;
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

    .selection-line {
        color: var(--be-muted);
        font-size: 0.86rem;
        line-height: 1.5;
    }

    .selection-title {
        color: var(--be-ink);
        font-size: 0.98rem;
        font-weight: 750;
        line-height: 1.35;
        margin-bottom: 0.2rem;
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
def load_index(path: str, mtime_ns: int) -> dict[str, Any]:
    _ = mtime_ns
    payload = orjson.loads(Path(path).read_bytes())

    packages = payload.get("packages", [])
    if not isinstance(packages, list):
        raise ValueError("data/emote_index.json does not contain a package list")

    return payload


@st.cache_data(show_spinner=False)
def package_frame(packages: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(packages)
    if frame.empty:
        return pd.DataFrame(columns=["id", "name", "_search_text"])

    frame["id"] = pd.to_numeric(frame["id"], errors="coerce").astype("Int64")
    frame["name"] = frame["name"].fillna("").astype(str)
    frame["_search_text"] = frame["id"].astype(str).str.cat(frame["name"], sep=" ").str.casefold()
    return frame


def init_state() -> None:
    st.session_state.setdefault(SELECTED_IDS_KEY, set())
    st.session_state.setdefault(PAGE_KEY, 1)


def selected_ids() -> set[int]:
    current_ids = st.session_state.setdefault(SELECTED_IDS_KEY, set())
    if not isinstance(current_ids, set):
        current_ids = set(current_ids)
        st.session_state[SELECTED_IDS_KEY] = current_ids
    return current_ids


def prune_selected_ids(valid_ids: set[int]) -> None:
    current_ids = selected_ids()
    stale_ids = current_ids - valid_ids
    if not stale_ids:
        return

    current_ids.difference_update(stale_ids)
    for package_id in stale_ids:
        st.session_state.pop(checkbox_key(package_id), None)
    invalidate_archive()


def selection_signature() -> tuple[int, ...]:
    return tuple(sorted(selected_ids()))


def checkbox_key(package_id: int) -> str:
    return f"select_{package_id}"


def invalidate_archive() -> None:
    st.session_state.pop(ARCHIVE_BYTES_KEY, None)
    st.session_state.pop(ARCHIVE_NAME_KEY, None)
    st.session_state.pop(ARCHIVE_SELECTION_KEY, None)
    st.session_state.pop(ARCHIVE_REPORT_KEY, None)


def set_package_selected(package_id: int, selected: bool) -> None:
    current_ids = selected_ids()
    before = package_id in current_ids
    if selected:
        current_ids.add(package_id)
    else:
        current_ids.discard(package_id)
    st.session_state[checkbox_key(package_id)] = selected
    if before != selected:
        invalidate_archive()


def sync_package_checkbox(package_id: int) -> None:
    set_package_selected(package_id, bool(st.session_state.get(checkbox_key(package_id))))


def sync_checkbox_widget(package_id: int) -> None:
    st.session_state[checkbox_key(package_id)] = package_id in selected_ids()


def sync_batch_selection(option_ids: list[int]) -> None:
    current_ids = selected_ids()
    before = set(current_ids)
    option_id_set = set(option_ids)
    picked_ids = set(st.session_state.get(BATCH_SELECT_KEY, []))

    current_ids.difference_update(option_id_set)
    current_ids.update(picked_ids)
    for package_id in option_ids:
        sync_checkbox_widget(package_id)

    if current_ids != before:
        invalidate_archive()


def sync_page_selection(page_ids: list[int], key: str) -> None:
    selected = bool(st.session_state.get(key))
    for package_id in page_ids:
        set_package_selected(package_id, selected)


def type_badge(package: dict[str, Any]) -> str:
    labels = {
        1: "普通",
        2: "会员专属",
        3: "购买所得",
        4: "颜文字",
        12: "充电所得",
    }
    try:
        package_type = int(package.get("type") or 0)
    except (TypeError, ValueError):
        package_type = 0
    return str(labels.get(package_type) or package.get("type_label") or "未知")


def filter_packages(
    packages: list[dict[str, Any]],
    frame: pd.DataFrame,
    query: str,
) -> list[dict[str, Any]]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return packages

    mask = pd.Series(True, index=frame.index)
    for term in terms:
        mask &= frame["_search_text"].str.contains(term, regex=False, na=False)

    matched_ids = set(frame.loc[mask, "id"].dropna().astype(int))
    return [
        package
        for package in packages
        if int(package["id"]) in matched_ids
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


def render_page_jump_controls(
    page: int,
    max_page: int,
    visible_count: int,
    total_count: int,
) -> int:
    control_col, caption_col = st.columns([1.6, 3], vertical_alignment="center")
    with control_col:
        minus_col, input_col, plus_col = st.columns([0.7, 1.5, 0.7], vertical_alignment="bottom")
        with minus_col:
            if st.button(
                "-",
                icon=":material/remove:",
                disabled=page <= 1,
                key="page_minus",
                width="stretch",
                help="上一页",
            ):
                request_page(page - 1)
        with input_col:
            page = int(
                st.number_input(
                    "页码",
                    min_value=1,
                    max_value=max_page,
                    step=1,
                    key=PAGE_KEY,
                    help=PAGE_JUMP_HELP,
                )
            )
        with plus_col:
            if st.button(
                "+",
                icon=":material/add:",
                disabled=page >= max_page,
                key="page_plus",
                width="stretch",
                help="下一页",
            ):
                request_page(page + 1)

    with caption_col:
        st.markdown(
            f"<div class='toolbar-note'>当前显示 {visible_count:,} / {total_count:,} 个表情包，"
            f"第 {page:,} / {max_page:,} 页。</div>",
            unsafe_allow_html=True,
        )

    return page


def package_by_id(packages: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(package["id"]): package for package in packages}


def trusted_image_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return None
    if not any(
        host == suffix or host.endswith(f".{suffix}") for suffix in TRUSTED_IMAGE_HOST_SUFFIXES
    ):
        return None

    return urlunparse(parsed._replace(scheme="https"))


def render_header(index: dict[str, Any], packages: list[dict[str, Any]]) -> None:
    updated_at = str(index.get("updated_at") or "").replace("T", " ").replace("Z", " UTC")
    st.markdown(
        f"""
        <div class="app-kicker">BILIBILI EMOTE ARCHIVE</div>
        <h1 class="app-title">Bilibili 表情包下载器</h1>
        <div class="app-subtitle">
        搜索每天自动更新的 Bilibili 表情包索引。勾选表情包后，点击生成按钮才会由运行 Streamlit 的服务端获取公开图片和元数据，并打包为 zip 供浏览器保存。
        </div>
        <div class="stat-strip">
            <div class="stat-chip"><span>索引</span>{len(packages):,} 个</div>
            <div class="stat-chip"><span>已选择</span>{len(selected_ids()):,} 个</div>
            <div class="stat-chip"><span>更新</span>{html.escape(updated_at)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_package_card(package: dict[str, Any], selected_ids: set[int]) -> None:
    package_id = int(package["id"])
    safe_name = html.escape(str(package["name"]))
    preview_url = str(package["preview_url"])
    safe_preview_url = trusted_image_url(preview_url)
    image_html = (
        f'<img src="{html.escape(safe_preview_url, quote=True)}" alt="{safe_name}" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
        if safe_preview_url
        else '<span class="emote-meta">预览加载失败</span>'
    )
    key = checkbox_key(package_id)
    checked = package_id in selected_ids
    st.session_state[key] = checked

    st.markdown(
        f"""
        <div class="emote-card">
            <div class="emote-cover">
                {image_html}
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

    st.checkbox(
        "选择",
        key=key,
        on_change=sync_package_checkbox,
        args=(package_id,),
        label_visibility="collapsed",
    )


def render_grid(packages: list[dict[str, Any]], page: int, page_size: int) -> None:
    current_ids = selected_ids()
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
                render_package_card(package, current_ids)


def render_pagination(current_page: int, max_page: int, key_prefix: str) -> None:
    if max_page <= 1:
        return

    left_col, center_col, right_col = st.columns([2, 1, 2], vertical_alignment="center")
    with left_col:
        with st.container(horizontal=True, gap="small"):
            if st.button("首页", disabled=current_page <= 1, key=f"{key_prefix}_first", icon=":material/first_page:"):
                request_page(1)
            if st.button("上一页", disabled=current_page <= 1, key=f"{key_prefix}_previous", icon=":material/chevron_left:"):
                request_page(current_page - 1)
    with center_col:
        st.markdown(
            f"<div class='toolbar-note' style='text-align:center;'>第 {current_page} / {max_page} 页</div>",
            unsafe_allow_html=True,
        )
    with right_col:
        with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if st.button("下一页", disabled=current_page >= max_page, key=f"{key_prefix}_next", icon=":material/chevron_right:"):
                request_page(current_page + 1)
            if st.button("末页", disabled=current_page >= max_page, key=f"{key_prefix}_last", icon=":material/last_page:"):
                request_page(max_page)


def make_booru_client(timeout: float = 60.0 * 5, pool_size: int = MEDIA_CONCURRENCY) -> Booru:
    return Booru(
        logger_level=logging.ERROR,
        base_url=REFERER,
        proxies=None,
        trust_env=False,
        max_attempt_number=3,
        retries=3,
        rate_limit=None,
        timeout=timeout,
        pool_connections=pool_size,
        pool_maxsize=pool_size,
    )


async def fetch_package_detail(
    client: Booru,
    package: dict[str, Any],
) -> dict[str, Any]:
    package_id = int(package["id"])
    response = await client.get(
        DETAIL_API,
        params={
            "business": "reply",
            "ids": package_id,
        },
        referer=REFERER,
    )

    payload = response.json()
    if payload.get("code") != 0:
        message = payload.get("message") or payload.get("msg") or "unknown error"
        raise RuntimeError(f"Package {package_id} failed: {message}")

    packages = payload.get("data", {}).get("packages", [])
    if not packages:
        raise RuntimeError(f"Package {package_id} returned no detail")

    return packages[0]


async def fetch_package_detail_job(
    client: Booru,
    package: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return package, await fetch_package_detail(client, package)


async def fetch_emote_bytes(
    client: Booru,
    package_id: int,
    package_name: str,
    emote: dict[str, Any],
    emote_url: str,
) -> tuple[str, bytes, str]:
    emote_name = normalize_filepath(str(emote.get("text") or emote.get("id") or "emote"))
    emote_path = (
        f"bilibili-emote/images/{package_id}_{normalize_filepath(package_name)}/"
        f"{emote_name}{extension_from_url(emote_url)}"
    )
    response = await client.get(emote_url, referer=REFERER)

    return emote_path, response.content, emote_name


def extension_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    return ".png"


ProgressCallback = Callable[[float, str, str | None], None]


def is_text_package(package: dict[str, Any]) -> bool:
    return int(package.get("type") or 0) == 4


async def build_zip_async(
    selected_packages: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
) -> tuple[bytes, dict[str, Any]]:
    client = make_booru_client()
    buffer = io.BytesIO()
    details: list[tuple[dict[str, Any], dict[str, Any]]] = []
    failures: list[str] = []
    skipped_texts = 0
    succeeded_emotes = 0

    try:
        if progress_callback:
            progress_callback(0.0, "准备获取表情包明细", None)

        downloadable_packages: list[dict[str, Any]] = []
        for package in selected_packages:
            if is_text_package(package):
                skipped_texts += 1
                if progress_callback:
                    progress_callback(
                        0.0,
                        "跳过颜文字包",
                        f"[{int(package['id'])}] {package['name']}: 颜文字包不包含图片资源，已跳过",
                    )
            else:
                downloadable_packages.append(package)

        if not downloadable_packages:
            if progress_callback:
                progress_callback(1.0, "没有可下载图片", "选中的表情包均为颜文字，未生成压缩包")
            raise RuntimeError("选中的表情包均为颜文字，不包含可下载图片资源。")

        detail_tasks = [
            asyncio.create_task(fetch_package_detail_job(client, package))
            for package in downloadable_packages
        ]
        for completed_index, task in enumerate(asyncio.as_completed(detail_tasks), start=1):
            try:
                package, detail = await task
            except Exception as exc:
                message = f"明细获取失败: {exc.__class__.__name__}: {exc}"
                failures.append(message)
                if progress_callback:
                    progress_callback(
                        DETAIL_PROGRESS_WEIGHT * completed_index / len(detail_tasks),
                        f"明细 {completed_index}/{len(detail_tasks)}",
                        message,
                    )
            else:
                package_id = int(package["id"])
                package_name = str(package["name"])
                details.append((package, detail))
                if progress_callback:
                    progress_callback(
                        DETAIL_PROGRESS_WEIGHT * completed_index / len(detail_tasks),
                        f"明细 {completed_index}/{len(detail_tasks)}: {package_name}",
                        f"[{package_id}] 已获取 {package_name} 明细",
                    )

        media_jobs: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]] = []
        for package, detail in details:
            package_id = int(package["id"])
            package_name = str(package["name"])
            if int(detail.get("type") or package.get("type") or 0) == 4:
                skipped_texts += 1
                if progress_callback:
                    progress_callback(
                        DETAIL_PROGRESS_WEIGHT,
                        "跳过颜文字包",
                        f"[{package_id}] {package_name}: 明细标记为颜文字包，已跳过图片下载",
                    )
                continue

            for emote in detail.get("emote", []) or []:
                if not isinstance(emote, dict):
                    continue

                emote_name = str(emote.get("text") or emote.get("id") or "emote")
                emote_url = str(emote.get("url") or "")
                safe_emote_url = trusted_image_url(emote_url)
                if int(emote.get("type") or 0) == 4 or safe_emote_url is None:
                    skipped_texts += 1
                    if progress_callback:
                        progress_callback(
                            DETAIL_PROGRESS_WEIGHT,
                            "跳过文本表情",
                            f"[{package_id}] {package_name}: 跳过文本表情 {emote_name}",
                        )
                    continue

                media_jobs.append((package, detail, emote, safe_emote_url))

        if not details:
            if progress_callback:
                progress_callback(1.0, "生成失败", "未能获取任何表情包明细")
            raise RuntimeError("未能获取任何表情包明细，无法生成压缩包。")

        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            for package, detail in details:
                package_id = int(package["id"])
                package_name = str(package["name"])
                json_path = f"bilibili-emote/jsons/{package_id}_{normalize_filepath(package_name)}.json"
                archive.writestr(
                    json_path,
                    orjson.dumps(detail, option=orjson.OPT_INDENT_2).decode("utf-8") + "\n",
                )

            if not media_jobs:
                if progress_callback:
                    progress_callback(1.0, "没有可下载图片，已写入元数据", None)
                return buffer.getvalue(), {
                    "packages": len(details),
                    "emotes": 0,
                    "skipped_texts": skipped_texts,
                    "failures": failures,
                }

            media_tasks = [
                asyncio.create_task(
                    fetch_emote_bytes(
                        client,
                        int(package["id"]),
                        str(package["name"]),
                        emote,
                        emote_url,
                    )
                )
                for package, _, emote, emote_url in media_jobs
            ]
            for completed_index, task in enumerate(asyncio.as_completed(media_tasks), start=1):
                try:
                    emote_path, content, emote_name = await task
                except Exception as exc:
                    message = f"图片下载失败: {exc.__class__.__name__}: {exc}"
                    failures.append(message)
                    if progress_callback:
                        progress_callback(
                            DETAIL_PROGRESS_WEIGHT
                            + (1 - DETAIL_PROGRESS_WEIGHT) * completed_index / len(media_tasks),
                            f"图片 {completed_index}/{len(media_tasks)}",
                            message,
                        )
                    continue

                archive.writestr(emote_path, content)
                succeeded_emotes += 1
                if progress_callback:
                    progress_callback(
                        DETAIL_PROGRESS_WEIGHT
                        + (1 - DETAIL_PROGRESS_WEIGHT) * completed_index / len(media_tasks),
                        f"图片 {completed_index}/{len(media_tasks)}: {emote_name}",
                        None,
                    )

        return buffer.getvalue(), {
            "packages": len(details),
            "emotes": succeeded_emotes,
            "skipped_texts": skipped_texts,
            "failures": failures,
        }
    finally:
        await client.client.close()


def build_zip(
    selected_packages: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
) -> tuple[bytes, dict[str, Any]]:
    return asyncio.run(build_zip_async(selected_packages, progress_callback))


def selected_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = package_by_id(packages)
    current_ids = sorted(selected_ids())
    return [by_id[package_id] for package_id in current_ids if package_id in by_id]


def option_label(package_id: int, packages_by_id: dict[int, dict[str, Any]]) -> str:
    package = packages_by_id[package_id]
    return f"{package_id} · {package['name']}"


def sync_filtered_multiselect(
    filtered: list[dict[str, Any]],
    packages_by_id: dict[int, dict[str, Any]],
) -> None:
    filtered_ids = [int(package["id"]) for package in filtered]
    current_ids = selected_ids()
    option_ids = [
        package_id
        for package_id in dict.fromkeys(filtered_ids + sorted(current_ids))
        if package_id in packages_by_id
    ]
    st.session_state[BATCH_SELECT_KEY] = [
        package_id for package_id in option_ids if package_id in current_ids
    ]

    st.multiselect(
        "批量选择表情包",
        options=option_ids,
        format_func=lambda package_id: option_label(package_id, packages_by_id),
        help=BATCH_SELECT_HELP,
        placeholder="输入 ID 或名称后，在这里批量添加或移除匹配结果",
        key=BATCH_SELECT_KEY,
        on_change=sync_batch_selection,
        args=(option_ids,),
    )


def render_page_select_all(page_items: list[dict[str, Any]], page: int) -> None:
    if not page_items:
        return

    page_ids = [int(package["id"]) for package in page_items]
    current_ids = selected_ids()
    all_selected = all(package_id in current_ids for package_id in page_ids)
    key = f"select_all_page_{page}_{len(page_items)}_{page_ids[0]}_{page_ids[-1]}"
    st.session_state[key] = all_selected
    st.checkbox(
        "选择当前页全部",
        help=PAGE_SELECT_HELP,
        key=key,
        on_change=sync_page_selection,
        args=(page_ids, key),
    )


def render_archive_report(report: dict[str, Any] | None, archive_ready: bool) -> None:
    if not report:
        return

    failure_count = len(report.get("failures", []))
    skipped_count = int(report.get("skipped_texts") or 0)
    if archive_ready:
        summary = (
            f"已生成 {report.get('packages', 0):,} 个包、"
            f"{report.get('emotes', 0):,} 张图片"
        )
    else:
        summary = "压缩包未生成"

    extras: list[str] = []
    if skipped_count:
        extras.append(f"{skipped_count:,} 个颜文字/文本项已跳过")
    if failure_count:
        extras.append(f"{failure_count:,} 条失败记录")
    if extras:
        summary += "，" + "，".join(extras)
    summary += "。"

    st.caption(summary)
    logs = list(report.get("logs", []))
    if logs or failure_count:
        with st.expander("生成日志", expanded=False, icon=":material/receipt_long:"):
            if logs:
                st.code("\n".join(logs), language="text")
            for failure in report.get("failures", []):
                st.error(str(failure), icon=":material/error:")


def render_download_panel(packages: list[dict[str, Any]]) -> None:
    current_selection = selected_packages(packages)
    selected_count = len(current_selection)
    current_signature = selection_signature()
    if st.session_state.get(ARCHIVE_SELECTION_KEY) != current_signature:
        invalidate_archive()
    archive_ready = ARCHIVE_BYTES_KEY in st.session_state

    with st.container(border=True):
        summary_col, prepare_col, save_col = st.columns(
            [3.2, 1.05, 1.05],
            vertical_alignment="center",
        )
        with summary_col:
            st.markdown(
                f"<div class='selection-title'>已选择 {selected_count:,} 个表情包</div>",
                unsafe_allow_html=True,
            )
            if selected_count:
                names = "、".join(str(package["name"]) for package in current_selection[:6])
                if selected_count > 6:
                    names += f" 等 {selected_count} 个"
                st.markdown(
                    f"<div class='selection-line'>{html.escape(names)}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div class='selection-line'>从搜索结果或全量索引中勾选表情包后即可生成压缩包。</div>",
                    unsafe_allow_html=True,
                )
            st.caption("点击“生成压缩包”后才会开始服务端下载和内存打包。")
            report = st.session_state.get(ARCHIVE_REPORT_KEY)
            render_archive_report(report, archive_ready)

        with prepare_col:
            prepare = st.button(
                "生成压缩包",
                icon=":material/archive:",
                help=PREPARE_ARCHIVE_HELP,
                disabled=selected_count == 0,
                width="stretch",
            )

        with save_col:
            st.download_button(
                "保存压缩包",
                data=st.session_state.get(ARCHIVE_BYTES_KEY, b""),
                file_name=st.session_state.get(ARCHIVE_NAME_KEY, "bilibili-emote.zip"),
                mime="application/zip",
                icon=":material/download:",
                help=SAVE_ARCHIVE_HELP,
                disabled=not archive_ready,
                width="stretch",
            )

    if prepare and current_selection:
        log_lines: list[str] = []
        with st.status("正在生成压缩包", expanded=False, type="compact") as status:
            progress_bar = st.progress(0, text="准备开始")
            log_placeholder = st.empty()

            def update_progress(value: float, text: str, log_line: str | None) -> None:
                progress_bar.progress(min(max(value, 0.0), 1.0), text=text)
                if log_line:
                    log_lines.append(log_line)
                    log_placeholder.code("\n".join(log_lines[-24:]), language="text")

            try:
                archive, report = build_zip(current_selection, update_progress)
            except RuntimeError as exc:
                st.session_state[ARCHIVE_SELECTION_KEY] = current_signature
                st.session_state[ARCHIVE_REPORT_KEY] = {
                    "packages": 0,
                    "emotes": 0,
                    "skipped_texts": 0,
                    "failures": [str(exc)],
                    "logs": log_lines.copy(),
                }
                status.update(label="生成失败", state="error")
                st.error(str(exc))
            except Exception as exc:
                st.session_state[ARCHIVE_SELECTION_KEY] = current_signature
                st.session_state[ARCHIVE_REPORT_KEY] = {
                    "packages": 0,
                    "emotes": 0,
                    "skipped_texts": 0,
                    "failures": [f"{exc.__class__.__name__}: {exc}"],
                    "logs": log_lines.copy(),
                }
                status.update(label="生成失败", state="error")
                st.error(f"{exc.__class__.__name__}: {exc}")
            else:
                st.session_state[ARCHIVE_BYTES_KEY] = archive
                st.session_state[ARCHIVE_NAME_KEY] = (
                    f"bilibili-emote-{dt.datetime.now(APP_TIMEZONE):%Y%m%d-%H%M%S}.zip"
                )
                st.session_state[ARCHIVE_SELECTION_KEY] = current_signature
                report["logs"] = log_lines.copy()
                st.session_state[ARCHIVE_REPORT_KEY] = report
                progress_bar.progress(1.0, text="压缩包已生成")
                status.update(label="压缩包已生成", state="complete")
                st.rerun()


def main() -> None:
    init_state()
    index = load_index(str(INDEX_PATH), INDEX_PATH.stat().st_mtime_ns)
    packages = list(index["packages"])
    frame = package_frame(packages)
    packages_by_id = package_by_id(packages)
    prune_selected_ids(set(packages_by_id))

    render_header(index, packages)

    st.markdown("<div class='section-heading'>筛选与选择</div>", unsafe_allow_html=True)
    with st.container(border=True):
        search_col, size_col = st.columns([3.4, 1], vertical_alignment="bottom")
        with search_col:
            query = st.text_input(
                "搜索 ID 或表情包名称",
                help=SEARCH_HELP,
                placeholder="例如：1 小黄脸 / 9888 小狐 / 2233",
            )
        with size_col:
            page_size = st.selectbox(
                "每页数量",
                PAGE_SIZE_OPTIONS,
                help=PAGE_SIZE_HELP,
                index=PAGE_SIZE_OPTIONS.index(DEFAULT_PAGE_SIZE),
            )

        filtered = filter_packages(packages, frame, query)
        max_page = page_count(len(filtered), page_size)
        if query and st.session_state.get("_last_query") != query:
            st.session_state[PAGE_KEY] = 1
        st.session_state["_last_query"] = query
        apply_pending_page(max_page)
        current_page = st.session_state[PAGE_KEY]
        current_page = render_page_jump_controls(current_page, max_page, len(filtered), len(packages))
        start = (current_page - 1) * page_size
        page_items = filtered[start : start + page_size]

        batch_col, page_select_col = st.columns([3.4, 1], vertical_alignment="bottom")
        with batch_col:
            sync_filtered_multiselect(filtered, packages_by_id)
        with page_select_col:
            render_page_select_all(page_items, current_page)

    st.markdown("<div class='section-heading'>下载</div>", unsafe_allow_html=True)
    render_download_panel(packages)

    st.markdown("<div class='section-heading'>浏览索引</div>", unsafe_allow_html=True)
    render_pagination(current_page, max_page, "top")
    render_grid(filtered, current_page, page_size)
    render_pagination(current_page, max_page, "bottom")


if __name__ == "__main__":
    main()
