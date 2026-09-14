#!/usr/bin/env python3
"""Y-Appstore 服务端：极简 F-Droid 仓库协议 + 网页上传管理。

仓库布局(data/repo)：
  index.xml          仓库总索引 (v1 F-Droid 格式, 客户端默认可读)
  repo.certificate   仓库公钥(HEX)
  apps/<pkg>/<vc>-<pkg>.apk        各版本 APK
  apps/<pkg>/metadata.xml          单 app 元数据 (v1 兼容字段)

管理端点：
  POST /admin/upload   上传一个 APK + 表单(pkg/name/versionCode/versionName/...)
      自动：存 apk -> 写 metadata.xml -> 重建 index.xml -> 用仓库私钥 gpg 签名
  GET  /admin         网页管理页(上传表单 + 当前仓库 app 列表)
  GET  /admin/registry.json   机器可读的当前仓库 app 清单
"""
import datetime
import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from flask import Flask, Response, jsonify, request, send_file

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
KEYS = os.path.join(DATA, "keys")
REPO = os.path.join(DATA, "repo")
APPS = os.path.join(REPO, "apps")
INDEX = os.path.join(REPO, "index.xml")
CERT_FILE = os.path.join(REPO, "repo.certificate")

GPG_HOME = KEYS

app = Flask(__name__)


def sha256hex(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dirs():
    for d in (KEYS, REPO, APPS):
        os.makedirs(d, exist_ok=True)


def gpg_sign_file(path):
    """对 path 生成清签字(ascii armor)，返回 (armored_text, rc)。"""
    out = path + ".gpg"
    r = subprocess.run(
        [
            "gpg",
            "--homedir", GPG_HOME,
            "--batch", "--yes", "--armor",
            "--output", out,
            "--detach-sign",
            "--local-user", "repo@yappstore.local",
            path,
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return "", r.returncode, r.stderr
    with open(out, "r") as f:
        return f.read(), 0, ""


def list_apps():
    """返回 [(pkg, versionCode, versionName, apkRelPath, apkHash, size, lastUpdate)]"""
    result = []
    if not os.path.isdir(APPS):
        return result
    for pkg in sorted(os.listdir(APPS)):
        pkgdir = os.path.join(APPS, pkg)
        if not os.path.isdir(pkgdir):
            continue
        for fn in sorted(os.listdir(pkgdir)):
            if not fn.endswith(".apk"):
                continue
            full = os.path.join(pkgdir, fn)
            m = re.match(r"^(\d+)-", fn)
            vc = int(m.group(1)) if m else 0
            result.append(
                {
                    "pkg": pkg,
                    "fileName": fn,
                    "rel": os.path.join("apps", pkg, fn),
                    "versionCode": vc,
                    "hash": sha256hex(full),
                    "size": os.path.getsize(full),
                    "lastUpdate": datetime.datetime.utcfromtimestamp(
                        os.path.getmtime(full)
                    ).strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
    return result


def read_meta(pkgdir, key, default=""):
    try:
        with open(os.path.join(pkgdir, "meta.json"), "r") as f:
            import json
            return json.load(f).get(key, default)
    except Exception:
        return default


def write_meta(pkgdir, meta):
    import json
    with open(os.path.join(pkgdir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


def app_xml_for(pkg, vc, vname, apk_rel, apk_hash, apk_size, meta):
    """生成单 app 的 F-Droid v1 app.xml。"""
    name = meta.get("name", pkg)
    desc = meta.get("description", "")
    author = meta.get("author", "")
    license_name = meta.get("license", "Unknown")
    apk_url = apk_rel  # 相对 /repo/ 根
    icon = meta.get("icon", "")
    now = datetime.datetime.now().strftime("%Y-%m-%d")
    min_sdk = meta.get("minSdk", "24")
    max_sdk = meta.get("maxSdk", "35")

    def ver_block(vn, vc):
        return f"""
    <version code="{vc}" name="{escape(vn)}">
      <date>{escape(now)}</date>
      <url>{escape(apk_url)}</url>
      <checksum type="sha256">{apk_hash}</checksum>
      <size>{apk_size}</size>
      <uses-permission android:name="android.permission.INTERNET" />
      <sdk min="{min_sdk}" target="{max_sdk}"></sdk>
    </version>
"""

    icon_tag = ""
    if icon:
        icon_tag = f'        <icon url="{escape(icon)}" />'

    return f"""<?xml version="1.0" encoding="utf-8"?>
<metadata>
  <versionCode>{vc}</versionCode>
  <versionName>{escape(vname)}</versionName>
  <minSdkVersion>{min_sdk}</minSdkVersion>
  <name>{escape(name)}</name>
  {icon_tag}
  <description><![CDATA[{desc}]]></description>
  <summary>{escape(name)}</summary>
  <license>{escape(license_name)}</license>
  <added>{escape(now)}</added>
  <last-updated>{escape(now)}</last-updated>
  <url /><changelog /><sourceCode /><issueTracker /><website />
  <donate />
  <author>{escape(author)}</author>
  <maintainer>{escape(author)}</maintainer>
  <translations/>
  <categories/>
  <screenshots/>
  <antifeatures/>
  <permissions/>
  <uses-features/>
  <localization/>
  <files-to-strip/>
  <version code="{vc}" name="{escape(vname)}">
    <date>{escape(now)}</date>
    <url>{escape(apk_url)}</url>
    <checksum type="sha256">{apk_hash}</checksum>
    <size>{apk_size}</size>
  </version>
</metadata>
"""


def rebuild_index():
    """重建 index.xml (v1 格式)。"""
    entries = []
    if not os.path.isdir(APPS):
        pass
    else:
        for pkg in sorted(os.listdir(APPS)):
            pkgdir = os.path.join(APPS, pkg)
            if not os.path.isdir(pkgdir):
                continue
            # 取该 app 里最新的一个 apk 作为该 app 代表
            apks = sorted(
                [f for f in os.listdir(pkgdir) if f.endswith(".apk")],
                key=lambda f: int(re.match(r"^(\d+)-", f).group(1))
                if re.match(r"^(\d+)-", f)
                else 0,
            )
            if not apks:
                continue
            fn = apks[-1]
            full = os.path.join(pkgdir, fn)
            m = re.match(r"^(\d+)-", fn)
            vc = int(m.group(1)) if m else 0
            vname = read_meta(pkgdir, "latestVersionName", fn)
            hash_v = sha256hex(full)
            size = os.path.getsize(full)
            entries.append((pkg, vc, vname, os.path.join("apps", pkg, fn), hash_v, size))

    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<repo url="" name="" last-updated="" last-updated-full="" last-updated-iso="">"]
    # 上面为占位, 实际使用下面正确的构造
    root_attrs = f'url="" name="Y-Appstore" last-updated="{datetime.datetime.now().strftime("%Y-%m-%d")}"'
    out = [f'<?xml version="1.0" encoding="utf-8"?>', f'<repo {root_attrs}>']
    for pkg, vc, vname, rel, h, size in entries:
        out.append(f'  <package name="{escape(pkg)}">')
        out.append(f'    <version code="{vc}" name="{escape(vname)}">')
        out.append(f'      <date>{datetime.datetime.now().strftime("%Y-%m-%d")}</date>')
        out.append(f'      <url>{escape(rel)}</url>')
        out.append(f'      <checksum type="sha256">{h}</checksum>')
        out.append(f'      <size>{size}</size>')
        out.append('    </version>')
        out.append('  </package>')
    out.append("</repo>")
    text = "\n".join(out)
    with open(INDEX, "w") as f:
        f.write(text)
    # 签名 index.xml
    sign_text, rc, err = gpg_sign_file(INDEX)
    if rc == 0:
        os.replace(INDEX + ".gpg", INDEX + ".gpg")
    return entries


@app.get("/repo/")
def repo_root():
    return redirect_index()


from flask import redirect, url_for


def redirect_index():
    return send_file(INDEX, mimetype="application/xml")


@app.get("/repo/<path:p>")
def repo_file(p):
    full = os.path.join(REPO, p)
    if os.path.exists(full) and os.path.isfile(full):
        if p.endswith(".apk"):
            return send_file(full, mimetype="application/vnd.android.package-archive")
        return send_file(full, mimetype="application/xml")
    return "not found", 404


@app.get("/repo.certificate")
def cert():
    if os.path.exists(CERT_FILE):
        return send_file(CERT_FILE, mimetype="application/octet-stream")
    return "no cert", 404


@app.get("/admin/registry.json")
def registry():
    return jsonify(list_apps())


HTML_ADMIN = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Y-Appstore 管理</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#222}
 h1{font-size:22px} .card{border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:12px 0}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}
 input,button{font-size:14px} .row{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}
 code{background:#f4f4f4;padding:2px 6px;border-radius:4px}
 .ok{color:#0a7a2f}.err{color:#b3261e}
</style>
</head>
<body>
<h1>Y-Appstore 仓库管理</h1>
<div class="card">
 <h3>上传 APK</h3>
 <form id="up" method="post" action="/admin/upload" enctype="multipart/form-data">
  <div class="row">
   <label>包名 <input name="pkg" required placeholder="com.example.app"></label>
   <label>名称 <input name="name" placeholder="My App"></label>
   <label>版本码 <input name="versionCode" required placeholder="1"></label>
   <label>版本号 <input name="versionName" placeholder="1.0"></label>
   <label>作者 <input name="author" placeholder="Yao"></label>
  </div>
  <div class="row">
   <label>最低SDK <input name="minSdk" value="24"></label>
   <label>最高SDK <input name="maxSdk" value="35"></label>
   <label>描述 <input name="description" placeholder="一句话"></label>
  </div>
  <div class="row">
   <input type="file" name="apk" required accept=".apk">
   <button type="submit">上传并入库</button>
  </div>
 </form>
 <p class="err" id="msg"></p>
</div>
<div class="card">
 <h3>当前仓库 <code id="count"></code></h3>
 <table id="tbl">
  <thead><tr><th>包名</th><th>版本</th><th>文件</th><th>大小</th><th>SHA256</th><th>更新</th></tr></thead>
  <tbody></tbody>
 </table>
</div>
<script>
fetch('/admin/registry.json').then(r=>r.json()).then(list=>{
  const tb=document.querySelector('#tbl tbody');
  document.getElementById('count').textContent=list.length+' 个包';
  list.forEach(a=>{
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+a.pkg+'</td><td>'+a.versionCode+'</td><td>'+a.fileName+'</td><td>'+(a.size/1048576).toFixed(2)+' MB</td><td><code>'+a.hash.slice(0,16)+'…</code></td><td>'+a.lastUpdate+'</td>';
    tb.appendChild(tr);
  });
});
</script>
</body>
</html>
"""


@app.get("/admin")
def admin():
    return HTML_ADMIN, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.post("/admin/upload")
def upload():
    f = request.files.get("apk")
    if not f or not f.filename:
        return "缺少 APK 文件", 400
    pkg = (request.form.get("pkg") or "").strip()
    vc = (request.form.get("versionCode") or "").strip()
    if not re.match(r"^[a-z0-9_.]+$", pkg) or not vc.isdigit():
        return "包名或版本码不合法", 400
    vname = request.form.get("versionName") or vc
    pkgdir = os.path.join(APPS, pkg)
    os.makedirs(pkgdir, exist_ok=True)

    ext = ".apk"
    fn = f"{vc}-{pkg}{ext}"
    dest = os.path.join(pkgdir, fn)
    f.save(dest)
    apk_hash = sha256hex(dest)
    apk_size = os.path.getsize(dest)

    # 更新 meta.json 保留最新
    import json
    meta_path = os.path.join(pkgdir, "meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as fh:
                meta = json.load(fh)
        except Exception:
            meta = {}
    for k in ("name", "description", "author", "minSdk", "maxSdk", "license"):
        if request.form.get(k):
            meta[k] = request.form.get(k)
    meta["latestVersionName"] = vname
    meta["latestVersionCode"] = int(vc)
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=2)

    # 写 app.xml (供 v2 客户端可读; 这里同时写一个 app.xml)
    app_xml = app_xml_for(pkg, int(vc), vname, os.path.join("apps", pkg, fn), apk_hash, apk_size, meta)
    with open(os.path.join(pkgdir, "app.xml"), "w") as fh:
        fh.write(app_xml)

    rebuild_index()
    return jsonify(
        ok=True,
        pkg=pkg,
        versionCode=int(vc),
        hash=apk_hash,
        size=apk_size,
        url=f"/repo/apps/{pkg}/{fn}",
    )


if __name__ == "__main__":
    ensure_dirs()
    # 首次启动若无 index.xml, 先建一份空索引+签名
    if not os.path.exists(INDEX):
        rebuild_index()
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Y-Appstore server on {host}:{port}")
    print(f"  repo root: /repo/  |  admin: /admin")
    app.run(host=host, port=port, threaded=True)