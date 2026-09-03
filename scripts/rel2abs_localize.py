#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 站点配置采集器 —— 相对→绝对→本地化（两步走）

流程：
  ① 访问接口 (base)/N ，得到含相对地址的原始配置  → 存 0.json
  ② 把 "./lib/xs.jar" 之类相对地址，按 base 拼成绝对地址 → 存 1.json
       （若配置中没有任何相对地址 → 直接进入第三步）
  ③ 遍历 1.json 里所有 http(s) 绝对地址，逐个下载本地化
  ④ 汇总索引 + git 托管

基准 base 规则：接口链接去掉末段文件名。例
   https://0.12yue.de5.net/5/0  → base = https://0.12yue.de5.net/5/
"""
import os, sys, json, re, time, hashlib, argparse
from urllib.parse import quote

try:
    import urllib.request
    import ssl
    CTX = ssl.create_default_context()
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE
except Exception:
    CTX = None

DEFAULT_BASE = "https://0.12yue.de5.net/5/"
DEFAULT_TARGETS = [
    ("0", "0"),
    ("1", "1"),
    ("2", "2"),
    ("3", "3"),
]

def log(msg): print(msg, flush=True)

def derive_base(api_url):
    """https://host/path/0  →  https://host/path/"""
    u = api_url.rstrip("/\\")
    idx = u.rfind("/")
    base = u[:idx+1] if idx > u.find("://") else u + "/"
    return base

def normalize_url(url):
    return (url or "").replace("\\", "/").strip()

def to_absolute(url, base):
    url = normalize_url(url)
    if not url: return url
    if re.match(r'^[a-z][a-z0-9+.-]*://', url, re.I): return url   # 已是绝对
    if url.startswith("//"): return "https:" + url
    if url.startswith("./"): url = url[2:]
    if url.startswith("/"):  return base.rstrip("/") + url
    return base + url

def safe_name(s):
    s = re.sub(r'\?.*$', '', s).rstrip("/")
    name = re.sub(r'^.*/', '', s)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', name)
    return name or "index"

def request_json(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        raw = r.read()
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except Exception:
        return text   # 非 JSON 也当作原始内容保存

def rel_to_abs_inplace(obj, base, converted):
    """递归把对象里所有 "./xxx" 相对地址改成绝对地址"""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                new = to_absolute(v, base)
                obj[k] = new
                converted.append((k, v, new))
            else:
                rel_to_abs_inplace(v, base, converted)
    elif isinstance(obj, list):
        for i in obj: rel_to_abs_inplace(i, base, converted)

def collect_abs_urls(obj, out):
    """递归收集对象里所有 http(s) 绝对地址"""
    def walk(o):
        if isinstance(o, dict):
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for i in o: walk(i)
        elif isinstance(o, str):
            for m in re.findall(r'https?://[^\s"\'\\]+', o):
                out.add(m)
    walk(obj)

def encode_url(url):
    """对非 ASCII / 特殊字符做百分编码，避免 latin-1 报错"""
    parts = url.split("?")
    path = quote(parts[0], safe="/:%")
    return path + ("?" + quote(parts[1], safe="=&/:%") if len(parts) > 1 else "")

def download(url, dest, delay=0.3):
    time.sleep(delay)
    req = urllib.request.Request(encode_url(url), headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        with open(dest, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1024*256)
                if not chunk: break
                f.write(chunk); total += len(chunk)
    return total

def unique_name(folder, name):
    """同名文件追加短哈希避免覆盖"""
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    if not os.path.exists(path): return name
    h = hashlib.md5(name.encode()).hexdigest()[:6]
    return f"{base}_{h}{ext}"

def process(api_url, out_root, max_files=50):
    name = safe_name(api_url.rstrip("/").split("/")[-1]) or "site"
    base = derive_base(api_url)
    folder = os.path.join(out_root, name)
    os.makedirs(folder, exist_ok=True)
    info = {"name": name, "api": api_url, "base": base, "ok": False, "assets": []}

    # ① 拉配置
    log(f"\n[1/3] 拉取接口: {api_url}")
    try:
        data = request_json(api_url)
    except Exception as e:
        log(f"  ✗ 接口不可达: {e}")
        info["error"] = str(e)
        # 兜底：若本地有 0.json（离线档）则继续
        seed = os.path.join(folder, "0.json")
        if os.path.exists(seed):
            log(f"  → 使用离线兜底档 {seed}")
            with open(seed, encoding="utf-8") as f: data = json.load(f)
        else:
            return info
    with open(os.path.join(folder, "0.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # ② 相对 → 绝对
    log(f"[2/3] 相对→绝对  (base={base})")
    converted = []
    if isinstance(data, (dict, list)):
        rel_to_abs_inplace(data, base, converted)
    if converted:
        for k, old, new in converted:
            log(f"    {k}: {old} → {new}")
    else:
        log("    无相对地址，直接进入下载步骤")
    with open(os.path.join(folder, "1.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    # ③ 逐个下载绝对地址
    urls = set()
    if isinstance(data, (dict, list)):
        collect_abs_urls(data, urls)
    log(f"[3/3] 发现 {len(urls)} 个绝对资源链接，开始下载（上限 {max_files}）")
    for i, u in enumerate(sorted(urls)):
        if i >= max_files:
            log(f"    已达上限 {max_files}，停止"); break
        fname = unique_name(folder, safe_name(u))
        try:
            n = download(u, os.path.join(folder, fname))
            info["assets"].append({"file": fname, "url": u, "bytes": n})
            log(f"    ✓ {fname}  ({n} B)")
        except Exception as e:
            info["assets"].append({"file": fname, "url": u, "error": str(e)})
            log(f"    ✗ {fname}: {e}")
    info["ok"] = True
    return info

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", nargs="*", default=None,
                    help="可选覆盖：名称|链接 列表，缺省用 DEFAULT_TARGETS")
    ap.add_argument("--out", default="resources")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    targets = args.targets or [f"{n}|{DEFAULT_BASE}{n}" for n, _ in DEFAULT_TARGETS]

    report = []
    for t in targets:
        if "|" in t:
            _name, url = t.split("|", 1)
        else:
            url = t
        info = process(url.strip(), args.out)
        report.append(info)

    with open(os.path.join(args.out, "batch_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 打印汇总
    log("\n" + "="*50 + "\n汇总")
    for r in report:
        status = "✓" if r.get("ok") else "✗"
        log(f"  {status} {r['name']}: {len(r.get('assets',[]))} 个资源  {r.get('error','')}")

if __name__ == "__main__":
    main()
