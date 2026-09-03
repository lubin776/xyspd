#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量资源采集托管脚本
====================
功能：请求 TVBox/猫影视 风格的资源接口，解析出资源清单，下载文件并托管到仓库。

输入（通过 GitHub Actions 的 workflow_dispatch 传入，或本地手动指定）：
  --targets "名称|链接"  （可重复，一行一组；默认内置两个接口）

TVBox 标准接口协议参考（http://cms.125.la/api/ 文档）：
  一级（首页分类）:  GET {api}?ac=list&pg=1           -> {"list":[ {...}, ... ]}
  二级（详情/播放）: GET {api}?ac=detail&ids=xxx      -> {"list":[ {"vod_play_url":"..."} ]}
  资源名称字段：     vod_name
  播放地址字段：     vod_play_url  (多集用 "#" 分隔，多线路用 "$$$" 分隔)
"""

import argparse, json, os, re, sys, time, urllib.request, urllib.parse
from pathlib import Path

# ============ 默认内置接口（只针对你的两个链接）============
DEFAULT_TARGETS = [
    ("简约导航", "https://0.12yue.de5.net/5/api2.json"),
    ("菠菜园",   "https://0.12yue.de5.net/5/api.json"),
]
# ==========================================================

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path(__file__).resolve().parent.parent))
OUT_ROOT = REPO_ROOT / "resources"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 30
SLEEP = 0.3          # 请求间隔（秒），礼貌抓取
MAX_FILES = 30       # 单接口最多下载文件数（防仓库膨胀）


def log(msg):
    print(f"[sync] {msg}", flush=True)


def safe_dirname(name: str) -> str:
    """把接口名称转成安全的文件夹名"""
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return name or "unnamed"


def normalize_url(url: str) -> str:
    """对 URL 中的非 ASCII / 特殊字符做百分编码，避免 urllib 报编码错误"""
    try:
        parts = urllib.parse.urlparse(url)
        path = urllib.parse.quote(parts.path, safe="/:%")
        query = urllib.parse.quote(parts.query, safe="=&:%#/")
        return urllib.parse.urlunparse((parts.scheme, parts.netloc, path, "", query, ""))
    except Exception:
        return url


def http_get(url: str, referer: str | None = None) -> bytes:
    url = normalize_url(url)
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = normalize_url(referer)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def fetch_json(url: str):
    """请求接口，优先当作 JSON 直链；失败时自动拼接 ?ac=list 再试（兼容标准接口）"""
    base = url.rstrip("/")
    candidates = [base]
    if "?" not in base:
        candidates.append(base + "?ac=list&pg=1")
    last_err = None
    for u in candidates:
        try:
            raw = http_get(u)
            text = raw.decode("utf-8", errors="replace").strip()
            return json.loads(text), u
        except Exception as e:
            last_err = e
    raise RuntimeError(f"所有请求方式都失败: {last_err}")


def save(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def extract_items(data) -> list[dict]:
    """自动探测 JSON 结构，抽出资源条目列表（兼容多种 schema）"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    # 常见路径
    for path_keys in [("list",), ("data", "list"), ("result", "list"),
                      ("data",), ("result",), ("videos",), ("items",)]:
        node = data
        ok = True
        for k in path_keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                ok = False
                break
        if ok and isinstance(node, list):
            return [x for x in node if isinstance(x, dict)]
    # 兜底：data 本身就是单条资源对象
    if "vod_name" in data or "vod_play_url" in data:
        return [data]
    return []


_URL_RE = re.compile(r"https?://[^$\s#]+", re.IGNORECASE)


def parse_play_urls(item: dict) -> list[str]:
    """从一条资源里抽出所有播放地址。

    兼容多种格式：
      - 直链：        https://x/y.m3u8
      - TVBox 多集：  url1#url2#url3
      - TVBox 多线路：线路1$urlA#urlB$$$线路2$urlC
    统一用正则提取所有 http/https 地址，最稳健。
    """
    urls: list[str] = []
    for k in ("vod_play_url", "vod_url", "play_url", "url"):
        raw = item.get(k)
        if not raw:
            continue
        found = _URL_RE.findall(str(raw))
        urls.extend(found)
    # 去重保序
    seen = set()
    uniq = []
    for u in urls:
        u = u.rstrip(".,;)")
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def download_file(url: str, dest: Path) -> bool:
    """下载单个文件；失败仅记录不中断"""
    try:
        raw = http_get(url, referer=dest.parent.name)
        save(dest, raw)
        return True
    except Exception as e:
        log(f"  ✗ 下载失败 {url[:80]}: {e}")
        return False


def sanitize_filename(s: str, ext_hint: str = "") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip(". ") or "item"
    if ext_hint and not s.lower().endswith(ext_hint.lower()):
        s += ext_hint
    return s[:120]


def guess_ext(url: str) -> str:
    url = url.split("?", 1)[0].split("#", 1)[0]
    if url.endswith(".m3u8"):
        return ".m3u8"
    if url.endswith(".mp4"):
        return ".mp4"
    if url.endswith(".json"):
        return ".json"
    return ""


def process_one(name: str, api_url: str) -> dict:
    """处理单个接口，返回统计 dict"""
    folder = OUT_ROOT / safe_dirname(name)
    folder.mkdir(parents=True, exist_ok=True)
    log(f"▶ 开始处理：{name}  →  {folder}")
    log(f"  接口：{api_url}")

    stat = {"name": name, "url": api_url, "ok": False, "items": 0,
            "files": 0, "error": ""}

    # 1. 请求接口，保存原始响应快照
    try:
        data, used_url = fetch_json(api_url)
        save(folder / "api_response.json",
             json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        log(f"  响应来源：{used_url}")
    except Exception as e:
        stat["error"] = str(e)
        log(f"  ✗ 接口请求失败：{e}")
        return stat

    # 2. 抽条目 + 解析播放地址
    items = extract_items(data)
    stat["items"] = len(items)
    log(f"  解析到资源条目：{len(items)} 条")

    index = []          # 资源索引清单
    downloaded = 0
    for it in items[:MAX_FILES]:
        title = it.get("vod_name") or it.get("name") or it.get("title") or "未命名"
        play_urls = parse_play_urls(it)
        index.append({
            "title": title,
            "id": it.get("vod_id"),
            "detail_url": it.get("vod_play_url") if not play_urls else None,
            "play_urls": play_urls,
        })
        # 3. 逐个播放地址下载（每条资源最多抓前几个地址，避免爆炸）
        for i, u in enumerate(play_urls[:3]):
            if downloaded >= MAX_FILES:
                break
            ext = guess_ext(u) or ".bin"
            fname = sanitize_filename(f"{title}_{i+1}", ext)
            if download_file(u, folder / fname):
                downloaded += 1
                time.sleep(SLEEP)

    stat["files"] = downloaded
    stat["ok"] = True

    # 4. 保存索引 + README
    save(folder / "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"))
    write_readme(folder, name, api_url, index, stat)
    log(f"  ✓ 完成：{len(items)} 条目 / {downloaded} 文件  →  {folder}")
    return stat


def write_readme(folder: Path, name: str, url: str, index: list, stat: dict):
    lines = [
        f"# {name}",
        "",
        f"- 接口：{url}",
        f"- 更新时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 资源条目：{stat['items']} 条",
        f"- 已下载文件：{stat['files']} 个",
        "",
        "## 资源清单",
        "",
    ]
    for it in index[:50]:
        lines.append(f"- **{it['title']}**  （{len(it['play_urls'])} 个播放地址）")
    if not index:
        lines.append("_未解析到资源条目_")
    readme = "\n".join(lines) + "\n"
    (folder / "README.md").write_text(readme, encoding="utf-8")


def parse_targets(raw: str) -> list[tuple[str, str]]:
    """解析 '名称|链接' 的多行输入"""
    targets = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, url = line.split("|", 1)
        else:
            url = line
            name = urllib.parse.urlparse(url).netloc or "unnamed"
        targets.append((name.strip(), url.strip()))
    return targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=str, default="",
                        help="多组 '名称|链接'，一行一个；不传则使用内置默认接口")
    args = parser.parse_args()

    if args.targets.strip():
        targets = parse_targets(args.targets)
    else:
        targets = DEFAULT_TARGETS

    log(f"共 {len(targets)} 个接口待采集")
    report = []
    for name, url in targets:
        try:
            st = process_one(name, url)
        except Exception as e:
            st = {"name": name, "url": url, "ok": False, "items": 0, "files": 0, "error": str(e)}
            log(f"  ✗ 异常：{e}")
        report.append(st)

    # 汇总报告
    md = ["# 采集报告\n", f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"]
    for st in report:
        status = "✅" if st["ok"] else "❌"
        md.append(f"- {status} **{st['name']}** — 条目 {st['items']} / 文件 {st['files']}"
                  + (f"  \n  错误：{st['error']}" if st["error"] else ""))
    (REPO_ROOT / "batch_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    ok = sum(1 for s in report if s["ok"])
    log(f"全部完成：{ok}/{len(report)} 成功")
    # 部分成功不算失败，方便 commit 继续
    sys.exit(0)


if __name__ == "__main__":
    main()
