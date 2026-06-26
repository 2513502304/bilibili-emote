"""
bilibili 获取所有表情包列表 API: https://socialsisteryi.github.io/bilibili-API-collect/docs/emoji/list.html#%E8%8E%B7%E5%8F%96%E6%8C%87%E5%AE%9A%E7%9A%84%E8%A1%A8%E6%83%85%E5%8C%85%E6%98%8E%E7%BB%86
bilibili 获取指定的表情包明细 API: https://socialsisteryi.github.io/bilibili-API-collect/docs/emoji/list.html#%E8%8E%B7%E5%8F%96%E6%8C%87%E5%AE%9A%E7%9A%84%E8%A1%A8%E6%83%85%E5%8C%85%E6%98%8E%E7%BB%86
"""

#!爬取 b 站所有表情包并下载

import logging
import os
import time

import aiofiles
from aiofiles import os as aioos
from aiofiles import tempfile as aiotempfile
import orjson
from spdl.pipeline import PipelineBuilder
from waifuboard import Booru
from waifuboard.utils import normalize_filepath

client = Booru(
    logger_level=logging.WARNING,
    base_url=(base_url := (referer := "https://www.bilibili.com")),
    proxies=None,
    trust_env=False,
    max_attempt_number=3,
    retries=3,
    rate_limit=None,
    timeout=60.0 * 5,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 要提取该仓库下某一个目录的文件路径
DEST_DIR = "./bilibili-emote"
IMAGE_PATH = f"{DEST_DIR}/images"
VIDEO_PATH = f"{DEST_DIR}/videos"
JSON_PATH = f"{DEST_DIR}/jsons"
for d in [IMAGE_PATH, VIDEO_PATH, JSON_PATH]:
    os.makedirs(d, exist_ok=True)


async def emote_index():
    # --- requests 获取所有表情包列表 API ---
    url = "https://api.bilibili.com/x/emote/setting/panel"  # 获取所有表情包列表 API

    headers = {
        "Cookie": "",  # 认证方式：Cookie（SESSDATA）
    }
    params = {
        "business": "reply",  # 使用场景，必要：reply：评论区；dynamic：动态
    }

    response = await client.get(
        url,
        headers=headers,
        params=params,
        referer=referer,
    )

    data: dict = response.json()

    emote_user_panel_packages: list[dict] = data["data"][
        "user_panel_packages"
    ]  # 用户拥有的表情包
    logger.info(f"[Index] Fetch {len(emote_user_panel_packages)} user packages")

    emote_all_packages: list[dict] = data["data"]["all_packages"]  # 所有表情包
    logger.info(f"[Index] Fetch {len(emote_all_packages)} all packages")

    # 收集所有表情包集合 id 列表
    for package in emote_all_packages:
        package_id: int = package[
            "id"
        ]  # 表情包集合 id，传递给“获取指定的表情包明细 API”以获取所有表情包
        package_name = package["text"]  # 表情包集合名称

        package_cover = package["url"]  # 表情包集合封面图
        package_mtime: int = package["mtime"]  # 创建时间。时间戳

        package_type: int = package[
            "type"
        ]  # 表情包集合类型。1：普通；2：会员专属；3：购买所得；4：颜文字（颜文字只有封面图为链接的形式，具体内部的表情包均为文本，例如："( \u309c- \u309c)\u3064\u30ed"）
        if package_type == 4:  #!不需要颜文字
            logger.warning(f"[Meta] Skip {package_id}: {package_name} because it's a text")
            continue

        package_attr: int = package["attr"]
        package_meta: dict = package[
            "meta"
        ]  # 属性信息（某些 type 下的 meta 字典中存在 item_url 字段，该字段为表情包集合的售卖链接，例如："https://www.bilibili.com/h5/mall/equity-link/collect-home?item_id=1679629702001&isdiy=0&part=emoji_package&from=emoji&f_source=garb&vmid=86137069&native.theme=1&navhide=1"
        package_flags: dict = package["flags"]  # 是否添加标志
        package_label: str | None = package["label"]
        package_package_sub_title: str = package["package_sub_title"]
        package_ref_mid: int = package["ref_mid"]
        package_resource_type: int = package["resource_type"]

        json_path = f"{JSON_PATH}/{package_id}_{normalize_filepath(package_name)}.json"

        if await aioos.path.exists(json_path):
            continue

        yield package_id, package_name, json_path


async def emote_detail(t: tuple[int, str, str]):
    package_id, package_name, json_path = t

    # --- requests 获取指定的表情包明细 API ---
    url = "https://api.bilibili.com/x/emote/package"  # 获取指定的表情包明细 API

    params = {
        "business": "reply",  # 使用场景，必要：reply：评论区；dynamic：动态
        "ids": package_id,  # 表情包集合 id，必要。id 之间以 , 隔开。!注意，ids 不能太多，否则会引发 {'code': -509, 'message': '请求过于频繁，请稍后再试', 'ttl': 1, 'data': None}。经测试，该值最大为 1500 左右
    }

    response = await client.get(
        url,
        params=params,
        referer=referer,
    )

    data: dict = response.json()

    assert (
        len(data["data"]["packages"]) == 1
    )  # 只提供了一个 package_id，而非多个以 , 隔开的 id
    package: dict = data["data"]["packages"][0]

    async with aiofiles.open(json_path, "wb") as f:
        await f.write(orjson.dumps(package))
    logger.info(f"[{package_id = }] Saved {package_name} metadata to {json_path}")

    emotes: list[dict] = package.get(
        "emote", []
    )  #!表情列表，可能会有不存在 emote 的 item

    if not emotes:
        logger.warning(f"[Image] Skip {package_id}: {package_name} because nothing to found")
        return

    emote_dir: str = os.path.join(
        IMAGE_PATH, f"{package_id}_{normalize_filepath(package_name)}"
    )
    await aioos.makedirs(emote_dir, exist_ok=True)

    # 表情包集合中的各个表情包
    for emote in emotes:
        emote_id: int = emote["id"]  # 表情包 id
        emote_package_id: int = emote["package_id"]  # 表情包集合 id
        emote_name: str = emote["text"]  # 表情转义符，颜文字时为该字串
        emote_url: str = emote["url"]  # 表情图片 url，颜文字时为该字串
        emote_mtime: int = emote["mtime"]  # 创建时间。时间戳
        emote_type: int = emote[
            "type"
        ]  # 表情包集合类型。1：普通；2：会员专属；3：购买所得；4：颜文字（颜文字只有封面图为链接的形式，具体内部的表情包均为文本，例如："( \u309c- \u309c)\u3064\u30ed"）；12：充电所得
        emote_attr: int = emote["attr"]
        emote_meta: dict = emote["meta"]  # 属性信息
        emote_flags: dict = emote["flags"]  # 禁用标志，无则为空
        emote_activity: bool | None = emote["activity"]

        emote_name: str = normalize_filepath(emote_name)
        emote_ext: str = os.path.splitext(emote_url)[-1]
        emote_path = os.path.join(emote_dir, emote_name + emote_ext)

        if await aioos.path.exists(emote_path):
            continue

        yield package_id, package_name, emote_url, emote_path


async def save_media(t: tuple[int, str, str, str]):
    package_id, package_name, emote_url, emote_path = t

    try:
        response = await client.get(
            emote_url,
            referer=referer,
        )

        async with aiofiles.open(emote_path, mode="wb") as f:
            await f.write(response.content)

        logger.info(
            f"[{package_id = }] Saved {package_name} {emote_url} to {emote_path}"
        )
        yield True

    except Exception as exc:
        logger.error(
            f"[{package_id = }] Failed to save {package_name} {emote_url} to {emote_path}, because the following exception was raised: {exc.__class__.__name__}: {exc}",
        )
        yield False


if __name__ == "__main__":
    pipeline = (
        PipelineBuilder()
        .add_source(emote_index())
        .pipe(emote_detail, concurrency=32)
        .pipe(save_media, concurrency=32)
        .add_sink(1)
        .build(num_threads=16)
    )

    start = time.perf_counter()
    succeed_counts, failure_counts = 0, 0

    for flag in pipeline:
        if flag:
            succeed_counts += 1
        else:
            failure_counts += 1

    end = time.perf_counter()
    logger.info(
        f"Time taken: {end - start} seconds, {succeed_counts = }, {failure_counts = }"
    )
