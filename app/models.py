# -*- coding: utf-8 -*-
"""
模型下载器（纯 HTTP，绕开 huggingface_hub 的缓存与符号链接机制）。

为什么要自己写：在 Windows 环境下，huggingface_hub 默认走「全局缓存 + 符号链接」
把文件映射到使用目录。当符号链接创建受限、或复用了上一次中断留下的空缓存文件时，
会得到一批 **0 字节的配置文件**，最终表现为莫名其妙的
`json.decoder.JSONDecodeError: Expecting value: line 1 column 1`。

本模块直接向镜像站请求文件列表并逐个下载到项目内的 data/models/ 目录，行为完全
可预期；下载完成后会做完整性校验，任何空文件都会被重新拉取。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, List, Optional

import httpx

from app import config

# 单文件下载超时：连接 15s，读取 300s（大权重文件较慢）
_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
_CHUNK = 256 * 1024

# 不需要的文件：其它推理框架的权重，体积大且用不上
_IGNORE_SUFFIX = (".onnx", ".h5", ".msgpack", ".ot", ".tflite", ".mlmodel", ".bin.zst")
_IGNORE_DIRS = ("onnx/", "openvino/", "coreml/", "tflite/", "onnxruntime/", "ov/")

# 必须存在且非空的文件（缺一不可，否则模型加载会失败）
_REQUIRED = ("config.json",)


def _is_ignored(filename: str) -> bool:
    low = filename.lower()
    if any(low.startswith(d) for d in _IGNORE_DIRS):
        return True
    if low.endswith(_IGNORE_SUFFIX):
        return True
    if low.endswith(".bin") and "bge" not in low:
        # pytorch_model.bin 与 model.safetensors 内容等价，有后者就不下前者
        return True
    return False


def model_dir(repo_id: str) -> Path:
    """把仓库名映射为本地目录：BAAI/bge-small-zh-v1.5 → BAAI--bge-small-zh-v1.5"""
    return config.MODELS_DIR / repo_id.replace("/", "--")


def _list_files(client: httpx.Client, repo_id: str) -> List[str]:
    """向镜像站要文件清单。"""
    url = f"{config.HF_ENDPOINT}/api/models/{repo_id}"
    r = client.get(url)
    r.raise_for_status()
    data = r.json()
    siblings = data.get("siblings") or []
    files = [s.get("rfilename") for s in siblings if s.get("rfilename")]
    if not files:
        raise RuntimeError(f"镜像站返回的文件清单为空：{repo_id}")
    return files


def _ok(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _download_file(client: httpx.Client, repo_id: str, filename: str,
                   dest: Path, verbose: bool) -> None:
    """下载单个文件，先写 .part 再原子替换，避免中断留下半截文件。"""
    url = f"{config.HF_ENDPOINT}/{repo_id}/resolve/main/{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    t0 = time.time()
    with client.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        big = total > 4 * 1024 * 1024
        last_pct = -5

        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(_CHUNK):
                f.write(chunk)
                done += len(chunk)
                if verbose and big and total:
                    pct = int(done * 100 / total)
                    if pct - last_pct >= 5:
                        last_pct = pct
                        print(f"\r      {filename}  {pct:3d}%  "
                              f"({done / 1048576:.1f}/{total / 1048576:.1f} MB)",
                              end="", flush=True)
        if verbose and big:
            print("\r" + " " * 72 + "\r", end="")

    # 服务端没给 content-length 时，至少要确认拿到了内容
    if not total and tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"下载到空文件：{filename}")

    tmp.replace(dest)
    if verbose:
        size = dest.stat().st_size / 1024
        unit, val = ("KB", size) if size < 1024 else ("MB", size / 1024)
        print(f"      [OK] {filename:<38} {val:7.1f} {unit}  {time.time() - t0:.1f}s",
              flush=True)


def _verify(target: Path) -> bool:
    """校验目录可用：必备文件存在且非空。"""
    for name in _REQUIRED:
        if not _ok(target / name):
            return False
    # 至少有一个权重文件
    has_weight = any(
        _ok(p) for p in (
            target / "model.safetensors",
            target / "pytorch_model.bin",
            target / "model.onnx",
        )
    )
    return has_weight


def _repair_empty_files(client: httpx.Client, repo_id: str, target: Path,
                        verbose: bool) -> int:
    """扫描目录，把 0 字节文件重新下载（自愈上一次中断/链接失败留下的坏文件）。"""
    fixed = 0
    for p in target.rglob("*"):
        if not p.is_file():
            continue
        if ".cache" in p.parts:          # 下载器自身的元数据目录
            continue
        if p.name.endswith(".part"):
            continue
        if p.stat().st_size > 0:
            continue
        rel = p.relative_to(target).as_posix()
        if verbose:
            print(f"      [修复] 检测到空文件 {rel}，重新下载", flush=True)
        try:
            _download_file(client, repo_id, rel, p, verbose)
            fixed += 1
        except Exception as e:
            print(f"      [警告] {rel} 修复失败：{e}", flush=True)
    return fixed


def ensure_model(repo_id: str, verbose: bool = True) -> Path:
    """
    确保模型已完整下载到本地，返回可用目录。
    已经完整时直接返回，不做任何网络请求。
    """
    target = model_dir(repo_id)

    if _verify(target):
        if verbose:
            print(f"      [OK] 模型已就绪：{target.name}")
        return target

    print(f"\n  正在准备模型 {repo_id}")
    print(f"      目标目录：{target}")

    headers = {"User-Agent": "campus-assistant/1.0"}
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                      headers=headers) as client:
        # 1) 首次使用 → 拉取文件清单并逐个下载
        files = _list_files(client, repo_id)
        todo = [f for f in files if not _is_ignored(f)]

        missing = [f for f in todo if not _ok(target / f)]
        if missing:
            print(f"      共 {len(todo)} 个文件，需下载 {len(missing)} 个")
            failed: List[str] = []
            for name in missing:
                try:
                    _download_file(client, repo_id, name, target / name, verbose)
                except Exception as e:
                    failed.append(name)
                    print(f"      [失败] {name}：{e}", flush=True)
            if failed:
                print(f"      {len(failed)} 个文件下载失败，稍后会自动重试")

        # 2) 自愈：无论什么原因留下的空文件，一律重新拉取
        _repair_empty_files(client, repo_id, target, verbose)

    if not _verify(target):
        raise RuntimeError(
            f"模型 {repo_id} 下载不完整，目录：{target}\n"
            f"请检查网络（当前镜像：{config.HF_ENDPOINT}）后重试，"
            f"或手动删除该目录重新构建索引。"
        )
    print(f"      [完成] {repo_id}")
    return target


def preload_all(verbose: bool = True) -> None:
    """预先下载问答所需的全部模型（嵌入 + 重排）。"""
    ensure_model(config.EMBED_MODEL, verbose)
    if config.ENABLE_RERANK:
        ensure_model(config.RERANK_MODEL, verbose)


if __name__ == "__main__":
    preload_all()
