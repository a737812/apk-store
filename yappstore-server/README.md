# Y-Appstore 服务端（极简 F-Droid 仓库服务）

把 Y-Appstore 客户端指向的这个服务端。提供：
- `/repo/`：F-Droid 仓库协议端点（`index.xml` + `apps/<pkg>/*.apk` 下载 + GPG 签名）
- `/admin`：网页上传管理（传 APK + 自动入库 + 重建索引 + 签名）

## 运行

```bash
cd yappstore-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 首次：生成仓库签名密钥 + 输出可填入客户端 default_repos.json 的公钥指纹
.venv/bin/python gen_keys.py

# 启动（默认 0.0.0.0:8080；Codespace 用端口转发暴露 8080）
.venv/bin/python server.py
```

启动后访问 `http://<你的域名>/repo/` 即为 F-Droid 仓库根。

## 仓库目录结构（data/ 下自动生成）

```
data/
  keys/            # repo keypair (gpg 二进制密钥 + 公钥导出)
  repo/
    index.xml      # 仓库总索引（v1 旧格式，客户端兼容）
    repo.certificate  # 仓库公钥(PEM) 供客户端校验
    apps/
      <pkg>/<versionCode>-<pkg>.apk
      <pkg>/metadata.xml
      <pkg>/app.xml
```
