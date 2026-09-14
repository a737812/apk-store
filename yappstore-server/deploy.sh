#!/bin/bash
# Y-Appstore 服务端一键部署: 装依赖 -> 生成/加载仓库密钥 -> 启动服务 -> 回填公钥到客户端 JSON -> 打印 URL
cd "$(dirname "$0")" || exit 1
mkdir -p data
python3 -m venv .venv 2>/dev/null || true
./.venv/bin/pip install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt

# 生成或加载 GPG 密钥
if [ ! -f data/repo/repo.certificate ]; then
  echo "[gen-keys] 生成 Y-Appstore 仓库签名密钥 ..."
  python3 gen_keys.py || { echo "密钥生成失败"; exit 1; }
fi

# 启动服务(后台, 监听 8080), 若已有旧进程则杀掉
pkill -f 'yappstore-server/server.py' 2>/dev/null
nohup python3 server.py > data/server.log 2>&1 &
sleep 3
echo "[run] 服务日志尾部:"; tail -n 5 data/server.log

# 回填公钥到客户端 default_repos.json (若存在且为占位符)
CERT=$(cat data/repo/repo.certificate 2>/dev/null)
CLient_rel="../fdroidclient/app/src/main/assets/default_repos.json"
if [ -f "$CLient_rel" ] && grep -q 'REPLACE_WITH_REPO_PUBLIC_KEY_HEX' "$CLient_rel"; then
  sed -i "s|REPLACE_WITH_REPO_PUBLIC_KEY_HEX_AFTER_RUNNING_GEN_KEYS|$CERT|g" "$CLient_rel"
  echo "[backfill] 已将仓库公钥回填到客户端 default_repos.json"
else
  echo "[backfill] 客户端 JSON 不存在或已非占位, 跳过. 请手动把下面 CERT 值填入 default_repos.json 的 certificate 字段:"
fi
echo ""
echo "=== CERT (publicKey hex, 填入客户端 certificate 字段) ==="
cat data/repo/repo.certificate 2>/dev/null | head -c 400; echo "..."
echo ""
echo "=== 仓库可访问端点 ==="
CS_URL="${CS_URL:-https://potential-space-happiness-5vggx6pjr6rxh46g5.github.dev}"
echo "  仓库根:   $CS_URL/repo/"
echo "  管理页:   $CS_URL/admin"
echo "  公钥:     $CS_URL/repo.certificate"
