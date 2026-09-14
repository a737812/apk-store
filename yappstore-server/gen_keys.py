#!/usr/bin/env python3
"""生成 Y-Appstore 仓库 GPG 签名密钥对，并把公钥导出为客户端可用的 certificate。

用法：
  python gen_keys.py            # 生成/更新密钥
  python gen_keys.py --check    # 只打印当前公钥指纹+证书（不生成新的）

生成后会把：
  data/keys/repo_key.gpg        # 签名私钥(二进制)
  data/repo/repo.certificate    # 公钥(PEM hex, 可直接填客户端 default_repos.json)
  data/repo/repo.sha256        # 公钥指纹(sha256 hex, 给客户端校验用)
"""
import argparse
import base64
import datetime
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
KEYS = os.path.join(DATA, "keys")
REPO = os.path.join(DATA, "repo")
KEY_GPG = os.path.join(KEYS, "repo_key.gpg")


def sh(args, quiet=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        sys.stderr.write(r.stderr)
    return r


def gpg_home(d):
    return ["--homedir", d, "--batch"]


def generate():
    os.makedirs(KEYS, exist_ok=True)
    os.makedirs(REPO, exist_ok=True)

    now = datetime.datetime.now()
    expire = "3650d"
    batch = f"""
%no-protection
Key-Type: RSA
Key-Length: 4096
Subkey-Type: RSA
Subkey-Length: 4096
Name-Real: Y-Appstore
Name-Email: repo@yappstore.local
Expire-Date: {expire}
Passphrase:
%commit
"""
    with open(os.path.join(KEYS, "batch.txt"), "w") as f:
        f.write(batch)

    r = sh(["gpg", *gpg_home(KEYS), "--gen-key", os.path.join(KEYS, "batch.txt")], quiet=False)
    if r.returncode != 0:
        print("gpg --gen-key failed:", r.stderr)
        return 1

    # 取指纹
    r = sh(["gpg", *gpg_home(KEYS), "--list-keys", "--fingerprint"])
    lines = r.stdout.splitlines()
    fingerprint = None
    for i, ln in enumerate(lines):
        if ln.strip() == "fingerprint:":
            fingerprint = lines[i + 1].strip()
            break
    if not fingerprint:
        print("无法取得指纹:", r.stdout)
        return 1
    print("仓库私钥指纹:", fingerprint)

    # 导出公钥(PEM)
    r = sh(["gpg", *gpg_home(KEYS), "--armor", "--export", "--default-key", fingerprint, "-o", os.path.join(KEYS, "repo_pub.gpg")])
    if r.returncode != 0:
        print("导出公钥失败:", r.stderr)
        return 1

    # 计算公钥 sha256 指纹
    with open(os.path.join(KEYS, "repo_pub.gpg"), "rb") as f:
        pub_pem = f.read()
    # 提取纯 base64 证书体(去掉 armor 头尾)，得裸 DER，再 hex
    b64 = pub_pem.decode("utf-8", "ignore")
    pem_body = "".join(b64.split("-----BEGIN PGP PUBLIC KEY BLOCK-----", 1)[1].split("-----END PGP PUBLIC KEY BLOCK-----", 1)[0].split())
    der = base64.b64decode(pem_body)
    cert_hex = der.hex()
    sha256_hex = hashlib.sha256(der).hexdigest()

    with open(os.path.join(REPO, "repo.certificate"), "w") as f:
        f.write(cert_hex)
    with open(os.path.join(REPO, "repo.sha256"), "w") as f:
        f.write(sha256_hex)

    print("\n==== 客户端 default_repos.json 可填入值 ====")
    print("certificate: " + cert_hex[:60] + "... " + cert_hex[-20:])
    print("完整值长度:", len(cert_hex))
    print("publicKeySha256: " + sha256_hex)
    print("\n请把上面两行完整值(去掉省略号)复制进客户端 app/src/main/assets/default_repos.json 的 certificate 字段。")
    return 0


def check():
    if not os.path.exists(KEY_GPG) and not os.path.exists(os.path.join(KEYS, "repo_pub.gpg")):
        print("尚未生成密钥，先运行: python gen_keys.py")
        return 1
    r = sh(["gpg", *gpg_home(KEYS), "--list-keys", "--fingerprint"])
    print(r.stdout)
    pub = os.path.join(KEYS, "repo_pub.gpg")
    if os.path.exists(pub):
        with open(pub, "rb") as f:
            data = f.read()
        b64 = data.decode("utf-8", "ignore")
        body = "".join(b64.split("-----BEGIN PGP PUBLIC KEY BLOCK-----", 1)[1].split("-----END PGP PUBLIC KEY BLOCK-----", 1)[0].split())
        der = base64.b64decode(body)
        print("publicKeySha256:", hashlib.sha256(der).hexdigest())
        print("certificate(hex):", der.hex())
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sys.exit(check() if a.check else generate())
