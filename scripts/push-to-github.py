#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
push-to-github.py —— 一次性把本套编译文件推到 GitHub 并触发 Actions 编译。

设计要点：用 Git Data API 走【单次原子提交】（blob → tree → commit → 更新 ref），
而不是逐个文件调 PUT /contents。原因：PUT /contents 每个文件产生一个 commit，
而工作流 `on.push.paths` 命中 build.yml / config / diy / scripts 任一即触发，
结果一次推送会拉起 N 份并发全量编译、互相抢占 runner。

用到的 REST API：
  1. POST  /user/repos                                  建仓（auto_init 先生成 main）
  2. GET   /repos/{o}/{r}/git/ref/heads/main            取当前 HEAD
  3. POST  /repos/{o}/{r}/git/blobs                     每个文件一个 blob
  4. POST  /repos/{o}/{r}/git/trees                     合成一棵树
  5. POST  /repos/{o}/{r}/git/commits                   合成一个 commit
  6. PATCH /repos/{o}/{r}/git/refs/heads/main           推进 ref（= 推送完成，触发 1 次编译）
  7. GET   /repos/{o}/{r}/actions/runs                  确认已触发
  8. POST  /repos/{o}/{r}/actions/workflows/.../dispatches   兜底手动触发

用法：
  GHPAT=<你的令牌> python push-to-github.py [仓库名] [--private]

需要的令牌权限（经典 PAT）：repo + workflow
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = next((a for a in sys.argv[1:] if not a.startswith("-")), "fanchmwrt")
PRIVATE = "--private" in sys.argv
DESC = "fanchmwrt 25.12.4 · Xiaomi AX3000T (stock layout) 定制固件 — GitHub Actions 云编译"
BRANCH = "main"

FILES = [
    ".github/workflows/build.yml",
    "config/ax3000t-stock.config",
    "diy-part1.sh",
    "scripts/prepare-immortalwrt-feeds.sh",
    "scripts/push-to-github.py",
    "README.md",
]

TOKEN = (os.environ.get("GHPAT") or os.environ.get("GITHUB_TOKEN") or "").strip()
if not TOKEN:
    sys.exit("错误：未设置 GHPAT 环境变量（把 Personal Access Token 传进来）")


def call(method, path, body=None, ok=(200, 201, 204)):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "openwrt-build-pusher")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code in ok:
            return e.code, {}
        try:
            msg = json.loads(raw).get("message", raw)
        except Exception:
            msg = raw
        return e.code, {"message": msg}


def step(n, text):
    print(f"\n[{n}] {text}")
    print("-" * 60)


# ---------------------------------------------------------------- 0. 校验令牌
step(0, "校验令牌身份与权限")
code, me = call("GET", "/user")
if code != 200:
    sys.exit(f"令牌无效：{code} {me.get('message')}")
OWNER = me["login"]
print(f"    登录身份：{OWNER}")

req = urllib.request.Request(API + "/user")
req.add_header("Authorization", "Bearer " + TOKEN)
req.add_header("User-Agent", "openwrt-build-pusher")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        scopes = r.headers.get("x-oauth-scopes", "")
except Exception:
    scopes = ""
if scopes:
    print(f"    令牌 scope：{scopes}")
    missing = [s for s in ("repo", "workflow") if s not in scopes]
    if missing:
        print(f"    ⚠️  缺少 {', '.join(missing)}；推送 .github/workflows/ 下文件必须要有 workflow")

# ---------------------------------------------------------------- 1. 建仓
step(1, f"创建仓库 {OWNER}/{REPO}（{'私有' if PRIVATE else '公开'}）")
code, r = call("GET", f"/repos/{OWNER}/{REPO}", ok=(200, 404))
if code == 200:
    print("    仓库已存在，跳过创建，直接复用")
else:
    code, r = call("POST", "/user/repos", {
        "name": REPO,
        "description": DESC,
        "private": PRIVATE,
        "auto_init": True,      # 生成 main 分支，Git Data API 需要有个 HEAD 可挂
        "has_issues": True,
        "has_wiki": False,
    })
    if code not in (200, 201):
        sys.exit(f"建仓失败：{code} {r.get('message')}")
    print(f"    已创建：{r.get('html_url')}")

# ---------------------------------------------------------------- 2. 取 HEAD
step(2, "读取当前 HEAD（auto_init 后 git 库可能还没就绪，带重试）")
head_sha = None
for attempt in range(1, 8):
    code, r = call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", ok=(200, 404, 409))
    if code == 200 and r.get("object", {}).get("sha"):
        head_sha = r["object"]["sha"]
        break
    print(f"    第 {attempt} 次未就绪（HTTP {code}），4 秒后重试…")
    time.sleep(4)
if not head_sha:
    sys.exit("拿不到 HEAD，仓库可能为空或分支不叫 main。请确认仓库已有初始提交。")
print(f"    HEAD = {head_sha[:12]}")

code, base_commit = call("GET", f"/repos/{OWNER}/{REPO}/git/commits/{head_sha}")
if code != 200:
    sys.exit(f"读取基线 commit 失败：{code} {base_commit.get('message')}")
base_tree = base_commit["tree"]["sha"]

# ---------------------------------------------------------------- 3. 建 blob
step(3, f"上传 {len(FILES)} 个文件为 blob")
tree_entries, failed = [], []
for f in FILES:
    local = os.path.join(ROOT, f.replace("/", os.sep))
    if not os.path.isfile(local):
        failed.append((f, "本地文件不存在"))
        print(f"    [跳过] {f} —— 本地不存在")
        continue
    with open(local, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    code, r = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                   {"content": b64, "encoding": "base64"})
    if code in (200, 201):
        tree_entries.append({"path": f, "mode": "100644", "type": "blob", "sha": r["sha"]})
        print(f"    [OK]   {f}")
    else:
        failed.append((f, r.get("message")))
        print(f"    [FAIL] {f} —— {code} {r.get('message')}")

if not tree_entries:
    sys.exit("没有任何文件可推送，终止。")

# ---------------------------------------------------------------- 4. 单次原子提交
step(4, "合成 tree → commit → 推进 ref（只产生 1 个 commit）")
code, r = call("POST", f"/repos/{OWNER}/{REPO}/git/trees",
               {"base_tree": base_tree, "tree": tree_entries})
if code not in (200, 201):
    sys.exit(f"建 tree 失败：{code} {r.get('message')}")
tree_sha = r["sha"]

code, r = call("POST", f"/repos/{OWNER}/{REPO}/git/commits", {
    "message": "build: AX3000T(stock) 定制固件编译方案\n\n"
               "编译器：fanchmwrt-25.12.4 / mediatek-filogic\n"
               "插件：passwall · homeproxy · easytier · frps · vlmcsd · rtp2httpd · ddns-dnspod · sftp-server",
    "tree": tree_sha,
    "parents": [head_sha],
})
if code not in (200, 201):
    sys.exit(f"建 commit 失败：{code} {r.get('message')}")
commit_sha = r["sha"]

code, r = call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
               {"sha": commit_sha, "force": False})
if code != 200:
    sys.exit(f"更新 ref 失败：{code} {r.get('message')}")
print(f"    已推送 commit {commit_sha[:12]} → {BRANCH}")

# ---------------------------------------------------------------- 5. 确认触发
step(5, "确认编译已触发")
time.sleep(10)
code, runs = call("GET", f"/repos/{OWNER}/{REPO}/actions/runs?per_page=5")
found = []
if code == 200:
    for run in runs.get("workflow_runs", []):
        if run.get("head_sha") == commit_sha or run["status"] in ("in_progress", "queued"):
            found.append(run)
            print(f"    #{run['run_number']} {run['name']} | {run['status']} | {run['html_url']}")

if not found:
    print("    未检测到运行，尝试 workflow_dispatch 兜底…")
    for i in range(5):
        code, r = call("POST",
                       f"/repos/{OWNER}/{REPO}/actions/workflows/build.yml/dispatches",
                       {"ref": BRANCH})
        if code in (200, 204):
            print("    已提交 workflow_dispatch")
            break
        # 仓库刚建时 GitHub 可能还没索引到 workflow，稍等再试
        print(f"    第 {i+1} 次 dispatch 失败（{code} {r.get('message')}），12 秒后重试")
        time.sleep(12)

# ---------------------------------------------------------------- 6. 汇总
step(6, "汇总")
print(f"    仓库   ：https://github.com/{OWNER}/{REPO}")
print(f"    Actions：https://github.com/{OWNER}/{REPO}/actions")
print(f"    commit ：{commit_sha}")
print(f"    已推送 {len(tree_entries)}/{len(FILES)} 个文件")
for f, why in failed:
    print(f"    ✗ {f} —— {why}")
if failed:
    sys.exit(2)
print("\n完成。已并入单次提交，只会触发 1 次编译，通常 1.5~2.5 小时。")
