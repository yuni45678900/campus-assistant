# -*- coding: utf-8 -*-
"""
站点级设置 —— 目前只有一项：学生端左上角那个校园地图入口。

管理端填地址 + 打开开关，学生端就会出现一个小图标，点开跳转到地图页。
不填 / 关掉，学生端一切照旧，不显示任何多余东西。

存 data/settings.json。地址只放行 http(s) —— 它是要当链接用的，
javascript: 之类的协议一律不收。
"""
from __future__ import annotations

import json
import threading
from typing import Dict

from app import config

_LOCK = threading.Lock()

DEFAULT: Dict = {
    "map_url": "",
    "map_enabled": False,
    "map_label": "校园地图",
}

MAX_URL = 500
MAX_LABEL = 20


def load() -> Dict:
    f = config.SETTINGS_FILE
    out = dict(DEFAULT)
    if not f.exists():
        return out
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[site] 设置读取失败，用默认值：{e}")
        return out
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULT})
    return out


def save(map_url: str, map_enabled: bool, map_label: str = "") -> Dict:
    url = (map_url or "").strip()[:MAX_URL]
    label = (map_label or "").strip()[:MAX_LABEL] or DEFAULT["map_label"]

    if url and not url.lower().startswith(("http://", "https://")):
        raise ValueError("地图地址要以 http:// 或 https:// 开头")

    # 没填地址就不算开启 —— 免得开关开着却点不动
    item = {
        "map_url": url,
        "map_enabled": bool(map_enabled) and bool(url),
        "map_label": label,
    }
    f = config.SETTINGS_FILE
    with _LOCK:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(f)
    return item


def public() -> Dict:
    """给前端读的版本：没开或没填地址就当没有，不往外暴露半截配置。"""
    s = load()
    enabled = bool(s["map_enabled"] and s["map_url"])
    return {
        "map_enabled": enabled,
        "map_url": s["map_url"] if enabled else "",
        "map_label": s["map_label"],
    }
