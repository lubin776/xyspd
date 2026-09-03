#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端测试 —— 用真实 api.json 结构验证：
  ① 采集白名单：只下 Spider/文本/图片，跳过 .m3u/.php 流媒体/网页；外链 csp_/https/Web 不采集
  ② 本地化 1.json：本地相对路径 -> lib/xxx；外链 & 标识原样保留
"""
import os, sys, json, importlib.util, shutil, threading, http.server, socketserver

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("M", os.path.join(HERE, "scripts", "rel2abs_localize.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

# ---------- 本地 mock 服务器（模拟 0.12yue.de5.net/5/lib/）----------
served = {
    "/5/lib/xs.jar": b"\xca\xfe\xba\xbe" + b"\x00" * 20,
    "/5/lib/jueson.jpg": b"\xff\xd8\xff" + b"\x00" * 20,
    "/5/lib/%E8%8F%A0%E8%8F%9C%E5%9B%AD%E4%B8%8B%E8%BD%BD.json": b'{"ok":1}',
    "/5/lib/%E4%BD%BF%E7%94%A8%E8%AF%B4%E6%98%8E.json": b'{"note":"readme"}',
    "/5/lib/result.m3u": b"#EXTM3U\n",   # 应被跳过
}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path in served:
            body = served[self.path]
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}/5/"

# 用真实 api.json
with open("/data/inputs/api.json", encoding="utf-8") as f:
    api_data = json.load(f)

OUT = os.path.join(HERE, "_test_out")
if os.path.exists(OUT): shutil.rmtree(OUT)

ok = True
def check(cond, msg):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + msg)
    ok = ok and cond

# ===== 测试1：采集扫描（不依赖网络，纯结构）=====
print("\n[测试1] 采集白名单 + 归拢到 lib/")
successes, failures = M.crawl(BASE, api_data, OUT)
check(os.path.isdir(os.path.join(OUT, "lib")), "lib/ 目录已创建")
for fn in ("xs.jar", "jueson.jpg", "菠菜园下载.json", "使用说明.json"):
    check(os.path.exists(os.path.join(OUT, "lib", fn)), f"lib/{fn} 已下载（含中文名）")
check(not any("result.m3u" in s for s in successes), "result.m3u 未采集（流媒体）")
check(all(s.startswith("lib/") for s in successes), "所有成功项都在 lib/ 下")
# 关键：确认外链字段没被当成下载目标
collected = M.collect_local_urls(api_data)
check("http://y.ds05.cn/zyapi.php" not in collected, "外链 ds05 api 未被采集")
check(not any("jx.xymp4" in u for u in collected), "解析接口 https 未被采集")
check(not any(u.startswith("csp_") for u in collected), "csp_ 标识未被采集")
check("./lib/result.m3u" in collected, "lives[].url ./lib/result.m3u 被正确扫描到（随后因 .m3u 跳过）")

# ===== 测试2：本地化 1.json =====
print("\n[测试2] 本地化 1.json —— 外链保留 + 本地路径改相对连接")
localized = M.localize_api(M.deepclone(api_data), successes)
check(localized["spider"] == "lib/xs.jar", f"spider: -> {localized['spider']}")
check(localized["logo"] == "lib/jueson.jpg", f"logo: -> {localized['logo']}")
# 下载失败的（result.m3u）保留原始路径
check(localized["lives"][0]["url"] == "./lib/result.m3u", "lives[].url 跳过 -> 保留原始 ./lib/result.m3u")
site_ext = next(s for s in localized["sites"] if s.get("key") == "本地包")
check(site_ext["ext"] == "lib/菠菜园下载.json", f"sites ext: -> {site_ext['ext']}")
site_api = next(s for s in localized["sites"] if s.get("key") == "说明")
check(site_api["api"] == "lib/使用说明.json", f"sites api(文本): -> {site_api['api']}")
# 外链 & 标识原样保留
ds05 = next(s for s in localized["sites"] if s.get("key") == "ds05")
check(ds05["api"] == "http://y.ds05.cn/zyapi.php", f"外链原样: {ds05['api']}")
check(any(s.get("api") == "csp_Douban" for s in localized["sites"]), "csp_Douban 标识保留")
check(localized["parses"][0]["url"] == "Web", "Web 标识保留")
check(localized["parses"][1]["url"].startswith("https://jx.xymp4"), "解析接口 https 保留")

# ===== 测试3：产物文件 =====
print("\n[测试3] 生成产物")
with open(os.path.join(OUT, "0.json"), "w", encoding="utf-8") as f:
    json.dump(api_data, f, ensure_ascii=False, indent=2)
with open(os.path.join(OUT, "1.json"), "w", encoding="utf-8") as f:
    json.dump(localized, f, ensure_ascii=False, indent=2)
check(os.path.exists(os.path.join(OUT, "0.json")), "0.json 已生成")
check(os.path.exists(os.path.join(OUT, "1.json")), "1.json 已生成")
rt = json.load(open(os.path.join(OUT, "1.json"), encoding="utf-8"))
check(rt["spider"] == "lib/xs.jar", "1.json 可被 TVBox 相对路径加载")

# 打印 1.json 关键片段供核对
print("\n--- 1.json 关键片段 ---")
print(json.dumps({k: rt[k] for k in ["spider", "logo"]}, ensure_ascii=False))
print(json.dumps([s for s in rt["sites"] if s["key"] in ("本地包", "说明")], ensure_ascii=False, indent=2))

print("\n" + ("全部通过 ✅" if ok else "存在失败 ❌"))
httpd.shutdown()
sys.exit(0 if ok else 1)
