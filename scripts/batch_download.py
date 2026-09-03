#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量资源采集脚本
========================
读取环境变量 RESOURCES（多组接口，每组一行，格式 "名称|链接"），
逐个：
  1. 创建 resources/<名称>/ 文件夹
  2. 请求 API 接口
  3. 解析资源下载链接（自动适配常见 JSON 结构）
  4. 下载文件到文件夹
  5. 生成 index.json / README.md

支持：
  - 任意 N 组接口（动态扩展，无需改代码）
  - 跳过空行 / 格式错误 / 重复名称
  - 每个接口下载数量上限 + 全局安全阀
  - 生成 batch_report.md 运行报告
"""

import os
import re
import sys
import json
import time
import urllib.parse
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("[FATAL] 缺少 requests，请先 pip install requests")
    sys.exit(1)


# ── 配置 ──────────────────────────────────────────────
MAX_FILES = int(os.environ.get("MAX_FILES", "20"))      # 单接口上限 (0=不限)
GLOBAL_MAX = int(os.environ.get("GLOBAL_MAX", "200"))    # 全局安全阀
ROOT = Path("resources")                                # 在 main() 中 mkdir

HTTP_TIMEOUT = 30
UA = "Mozilla/5.0 (compatible; ResourceSyncBot/1.0)"

# ── 工具函数 ──────────────────────────────────────────
def safe_name(name: str) -> str:
    """把名称转成安全的文件夹名"""
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())
    return name.strip("_") or "unnamed"


def parse_resources(text: str):
    """解析 '名称|链接' 列表，返回 [(name, url), ...]"""
    items = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            print(f"[WARN] 格式错误，已跳过: {line}")
            continue
        name, url = line.split("|", 1)
        name, url = name.strip(), url.strip()
        if not name or not url:
            print(f"[WARN] 名称为空或链接为空，已跳过: {line}")
            continue
        key = safe_name(name)
        if key in seen:
            print(f"[WARN] 重复名称，已跳过: {name}")
            continue
        seen.add(key)
        items.append((name, url, key))
    return items


def extract_links(data) -> list:
    """
    通用 JSON 资源链接提取器
    自动适配以下常见结构：
      - {"list": [{"vod_name", "vod_play_url"}, ...]}
      - {"data": {"list": [...]}}
      - [{"url": "..."}, ...]
      - {"url": "..."} / [直链字符串]
    返回 [(title, url), ...]
    """
    links = []

    def _collect(obj, depth=0):
        if depth > 6:
            return
        if isinstance(obj, dict):
            # 显式字段
            for k in ("url", "link", "src", "play_url", "vod_play_url", "download_url"):
                if k in obj and isinstance(obj[k], str) and obj[k].startswith(("http", "//")):
                    title = obj.get("title") or obj.get("name") or obj.get("vod_name") or ""
                    links.append((str(title), obj[k]))
            for v in obj.values():
                _collect(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item, depth + 1)

    _collect(data)

    # 去重
    seen = set()
    uniq = []
    for title, url in links:
        u = url.split("$")[0].split("#")[0]  # 去掉 m3u8 分隔符
        if u not in seen:
            seen.add(u)
            uniq.append((title, url))
    return uniq


def fetch_json(url: str):
    """请求 API，返回解析后的 JSON 或 None"""
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "json" in ctype or url.endswith(".json"):
            return r.json()
        # 非 JSON，当作单一直链处理
        return {"_raw_url": url, "list": [{"title": "index", "url": url}]}
    except Exception as e:
        print(f"[ERROR] 请求失败 {url}: {e}")
        return None


def download_file(url: str, dest: Path) -> bool:
    """下载单个文件到 dest，成功返回 True"""
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}, stream=True)
        r.raise_for_status()
        # 从 Content-Disposition 或 URL 推断文件名
        fname = None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            fname = m.group(1)
        if not fname:
            path = urllib.parse.urlparse(url).path
            fname = Path(path).name or "resource.bin"
        fname = re.sub(r"[\\/:*?\"<>|]+", "_", fname)
        target = dest / fname
        with open(target, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        return target.stat().st_size > 0
    except Exception as e:
        print(f"[ERROR] 下载失败 {url}: {e}")
        return False


def write_readme(folder: Path, name: str, url: str, links: list, success: int, failed: int):
    readme = folder / "README.md"
    lines = [
        f"# {name}",
        "",
        f"- **接口**: {url}",
        f"- **采集时间**: {datetime.utcnow().isoformat(timespec='seconds')}Z",
        f"- **资源总数**: {len(links)}（成功 {success}，失败 {failed}）",
        "",
        "## 资源列表",
        "",
        "| # | 名称 | 链接 |",
        "|---|------|------|",
    ]
    for i, (title, u) in enumerate(links[:100], 1):
        t = (title or u)[:60].replace("|", "\\|")
        lines.append(f"| {i} | {t} | {u} |")
    if len(links) > 100:
        lines.append(f"\n> 仅展示前 100 条，完整列表见 index.json（共 {len(links)} 条）")
    readme.write_text("\n".join(lines), encoding="utf-8")


# ── 主流程 ────────────────────────────────────────────
def main():
    ROOT.mkdir(exist_ok=True)

    raw = os.environ.get("RESOURCES", "")
    if not raw.strip():
        print("[FATAL] 未提供 RESOURCES 参数")
        sys.exit(1)

    items = parse_resources(raw)
    if not items:
        print("[FATAL] 没有解析到任何有效接口")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"共 {len(items)} 组接口待采集")
    print(f"单接口上限: {MAX_FILES if MAX_FILES > 0 else '不限'} | 全局上限: {GLOBAL_MAX}")
    print(f"{'='*60}\n")

    report = []
    total_files = 0
    global_count = 0

    for idx, (name, url, key) in enumerate(items, 1):
        print(f"\n[{idx}/{len(items)}] 📦 {name} ({url})")
        folder = ROOT / key
        folder.mkdir(exist_ok=True)

        # 1. 保存 API 原始响应
        data = fetch_json(url)
        if data is None:
            report.append((name, key, url, 0, 0, "❌ 接口请求失败"))
            continue

        api_snapshot = folder / "api_response.json"
        try:
            api_snapshot.write_text(
                json.dumps(data, ensure_ascii=False, indent=2)[:2_000_000],
                encoding="utf-8",
            )
        except Exception:
            pass

        # 2. 提取资源链接
        links = extract_links(data)
        print(f"    发现 {len(links)} 个资源链接")

        # 3. 下载文件
        limit = MAX_FILES if MAX_FILES > 0 else len(links)
        limit = min(limit, GLOBAL_MAX - global_count) if GLOBAL_MAX > 0 else limit
        success = failed = 0
        downloaded = []

        for i, (title, u) in enumerate(links[:limit]):
            if GLOBAL_MAX > 0 and global_count >= GLOBAL_MAX:
                print(f"    ⚠️ 达到全局上限 {GLOBAL_MAX}，停止")
                break
            print(f"    ↓ [{i+1}/{min(len(links), limit)}] {title or u}")
            if download_file(u, folder):
                success += 1
                downloaded.append((title, u))
            else:
                failed += 1
            global_count += 1
            time.sleep(0.3)  # 礼貌延迟，避免被封

        total_files += success

        # 4. 保存索引
        index = {
            "name": name,
            "key": key,
            "api_url": url,
            "synced_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "total": len(links),
            "downloaded": success,
            "failed": failed,
            "resources": [
                {"title": t, "url": u} for t, u in downloaded
            ],
        }
        (folder / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 5. 生成 README
        write_readme(folder, name, url, links, success, failed)

        status = "✅" if success > 0 else "⚠️"
        report.append((name, key, url, len(links), success, f"{status} {success} 成功 / {failed} 失败"))
        print(f"    ✔ 完成: {success} 成功, {failed} 失败")

    # ── 生成汇总报告 ──────────────────────────────────
    lines = [
        f"**采集时间**: {datetime.utcnow().isoformat(timespec='seconds')}Z  ",
        f"**接口总数**: {len(items)}  ",
        f"**下载文件总数**: {total_files}  ",
        "",
        "| 名称 | 文件夹 | 资源总数 | 下载结果 |",
        "|------|--------|----------|----------|",
    ]
    for name, key, url, total, ok, status in report:
        lines.append(f"| {name} | `{key}/` | {total} | {status} |")
    (Path("batch_report.md")).write_text("\n".join(lines), encoding="utf-8")

    # 输出给 workflow（commit message body）
    body = "\n".join(f"- {name}: {status}" for name, _, _, _, _, status in report)
    print(f"\n{'='*60}")
    print(f"全部完成，共下载 {total_files} 个文件")
    print(f"{'='*60}")
    print("REPORT_START")
    print(body)
    print("REPORT_END")

    # 供后续 step 读取（GitHub Actions output）
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"commit_body<<EOF\n{body}\nEOF\n")


if __name__ == "__main__":
    main()
