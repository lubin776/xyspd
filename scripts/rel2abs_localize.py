#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资源采集托管 —— 本地化生成器
=================================
两步走：
  ① 采集：只下载 Spider(.jar) / 文本(.json) / 图片，不下载网页(.html)与流媒体(.m3u/.mp4)
  ② 本地化 api：api.json 里「本地相对路径」(./lib/xxx) 改写成相对 api.json 的位置 (lib/xxx)，
     使其可直接用 file:// 或放在同目录 serve；外链 / csp_ / Web 等原样保留。

用法：
  python scripts/rel2abs_localize.py --seeds seeds --out resources
  python scripts/rel2abs_localize.py --targets "5_0|https://0.12yue.de5.net/5/0"
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse, hashlib

# ---------- 采集白名单（只下这三类）----------
ALLOWED_EXT = {
    "jar", "dex", "apk",                                      # spider
    "json", "js", "css", "txt", "xml", "csv", "yaml", "yml",  # 文本
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "ico", # 图片
}
# 明确跳过（即使不在白名单也打日志）
SKIP_EXT = {"html", "htm", "m3u", "m3u8", "mp4", "ts", "mpd", "ism", "php"}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DEFAULT_TIMEOUT = 30

# api.json 里“必定是路径/URL”的字段名；其余字符串（name/flag/rule 等）永不当路径
PATH_FIELDS = {"spider", "logo", "url", "api", "ext", "jar", "icon", "img", "image", "thumb"}
REL_PREFIXES = ("./", "../", "lib/", "./lib/")


# ==================== 工具 ====================
def get_extension(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return os.path.splitext(os.path.basename(path))[1].lower().lstrip(".")


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    safe_path = urllib.parse.quote(parts.path, safe="/:%+")
    safe_query = urllib.parse.quote(parts.query, safe="=&%+")
    return urllib.parse.urlunparse((parts.scheme, parts.netloc, safe_path, "", safe_query, ""))


def is_local_rel(s: str) -> bool:
    return isinstance(s, str) and s.strip().startswith(REL_PREFIXES)


def relpath_to_url(rel: str, base: str) -> str:
    rel = rel.lstrip("./").lstrip("/")
    return base.rstrip("/") + "/" + rel


def deepclone(o):
    return json.loads(json.dumps(o, ensure_ascii=False))


# ==================== ① 采集 ====================
def collect_local_urls(api_data) -> list[str]:
    """只采集 PATH_FIELDS 中且为「本地相对路径」的字段值"""
    urls: list[str] = []
    def visit(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in PATH_FIELDS and isinstance(v, str) and is_local_rel(v):
                    urls.append(v)
                else:
                    visit(v)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)
    visit(api_data)
    return urls


def download(url: str, dest_dir: str, seen: set) -> str | None:
    ext = get_extension(url)
    if ext in SKIP_EXT:
        print(f"    [跳过·流媒体/网页] .{ext}  {url}"); return None
    if ext not in ALLOWED_EXT:
        print(f"    [跳过·类型不符] .{ext}  {url}"); return None

    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(urllib.parse.urlparse(url).path) or "file"
    target = os.path.join(dest_dir, base)
    if os.path.exists(target) or base in seen:
        h = hashlib.md5(url.encode()).hexdigest()[:6]
        name, e = os.path.splitext(base)
        target = os.path.join(dest_dir, f"{name}_{h}{e}")
    seen.add(base)

    try:
        req = urllib.request.Request(normalize_url(url), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            data = r.read()
        if not data:
            print(f"    [跳过·空文件] {base}"); return None
        with open(target, "wb") as f:
            f.write(data)
        print(f"    [下载] {base}  ({len(data)//1024}KB)")
        return os.path.relpath(target, os.path.dirname(dest_dir)).replace("\\", "/")
    except Exception as e:
        print(f"    [失败] {base}: {e}"); return None


def crawl(base: str, api_data: dict, out_root: str) -> tuple[list[str], list[str]]:
    lib_dir = os.path.join(out_root, "lib")
    os.makedirs(lib_dir, exist_ok=True)
    seen: set[str] = set()
    successes, failures = [], []

    for rel in list(dict.fromkeys(collect_local_urls(api_data))):
        abs_url = relpath_to_url(rel, base)
        ext = get_extension(abs_url)
        if ext in SKIP_EXT:
            print(f"  [采集·跳过] {rel}  (流媒体/网页)"); continue
        if ext not in ALLOWED_EXT:
            print(f"  [采集·跳过] {rel}  (类型不在白名单)"); continue
        rel_out = download(abs_url, lib_dir, seen)
        if rel_out:
            lib_rel = os.path.join("lib", os.path.basename(rel_out)).replace("\\", "/")
            successes.append(lib_rel)
        else:
            failures.append(abs_url)
        time.sleep(0.3)
    return successes, failures


# ==================== ② 本地化 ====================
def localize_api(api_data, successes: list[str]) -> dict:
    """本地相对路径 -> lib/<filename>；外链 / csp_ / Web 原样保留。
    下载失败/跳过的：保留原始 ./lib/xxx，本地仍可继续用原地址。"""
    ok_set = set(successes)

    def rewrite(val: str) -> str:
        if not is_local_rel(val):
            return val
        target = "lib/" + os.path.basename(val.strip())
        return target if target in ok_set else val

    def visit(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in PATH_FIELDS and isinstance(v, str) and is_local_rel(v):
                    obj[k] = rewrite(v)
                else:
                    visit(v)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)
    visit(api_data)
    return api_data


# ==================== 主流程 ====================
def process_one(name: str, api_url: str, seeds_dir: str, out_root: str):
    print(f"\n{'='*60}\n[接口] {name}\n[链接] {api_url}\n{'='*60}")
    base = derive_base(api_url)
    print(f"[base] {base}")

    api_data = fetch_api(api_url, seeds_dir, name)
    if api_data is None:
        print("[!] 无法获取 api.json（接口不可达且无 seeds 兜底档），跳过")
        return {"name": name, "ok": 0, "fail": 0, "skipped": True}

    os.makedirs(out_root, exist_ok=True)
    with open(os.path.join(out_root, "0.json"), "w", encoding="utf-8") as f:
        json.dump(api_data, f, ensure_ascii=False, indent=2)

    successes, failures = crawl(base, api_data, out_root)

    localized = localize_api(deepclone(api_data), successes)
    with open(os.path.join(out_root, "1.json"), "w", encoding="utf-8") as f:
        json.dump(localized, f, ensure_ascii=False, indent=2)

    index = {"name": name, "source": api_url, "base": base,
             "collected": successes, "failed": failures, "localized_api": "1.json"}
    with open(os.path.join(out_root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\n[汇总] {name}: 成功 {len(successes)} | 失败/跳过 {len(failures)}")
    return {"name": name, "ok": len(successes), "fail": len(failures), "skipped": False}


def derive_base(api_url: str) -> str:
    p = urllib.parse.urlparse(api_url)
    return f"{p.scheme}://{p.netloc}{p.path}" if p.path.endswith("/") else f"{p.scheme}://{p.netloc}{os.path.dirname(p.path)}/"


def fetch_api(api_url: str, seeds_dir: str, name: str) -> dict | None:
    try:
        req = urllib.request.Request(normalize_url(api_url), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[接口不可达] {e}")
        seed = os.path.join(seeds_dir, name, "0.json")
        if os.path.exists(seed):
            print(f"[兜底] 使用 {seed}")
            with open(seed, encoding="utf-8") as f:
                return json.load(f)
        return None


def parse_targets(raw: str) -> list[tuple[str, str]]:
    out = []
    for line in (raw or "").splitlines():
        line = line.strip().strip("\r")
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            n, u = line.split("|", 1)
            out.append((n.strip(), u.strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="seeds")
    ap.add_argument("--out", default="resources")
    ap.add_argument("--targets", default="", help="name|url，换行分隔（兼容 workflow 传参）")
    args = ap.parse_args()

    defaults = [
        ("5_0", "https://0.12yue.de5.net/5/0"),
        ("5_1", "https://0.12yue.de5.net/5/1"),
        ("5_2", "https://0.12yue.de5.net/5/2"),
        ("5_3", "https://0.12yue.de5.net/5/3"),
    ]
    targets = parse_targets(args.targets) or defaults

    report = []
    for name, url in targets:
        out_root = os.path.join(args.out, name)
        report.append(process_one(name, url, args.seeds, out_root))

    lines = ["# 资源采集托管 · 运行报告\n"]
    for r in report:
        if r.get("skipped"):
            desc = "跳过（接口不可达）"
        else:
            desc = "成功 {} / 失败 {}".format(r["ok"], r["fail"])
        lines.append("- **{}** — {}".format(r["name"], desc))
    with open(os.path.join(args.out, "batch_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
