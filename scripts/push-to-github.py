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
    "message": "feat: 自建 apk 源（让固件能随便装源里的软件）+ 去 xray-core + sing-box-tiny\n\n"
               "编译器：fanchmwrt-25.12.4 / mediatek-filogic / xiaomi_mi-router-ax3000t (stock)\n"
               "\n"
               "【自建 apk 源】\n"
               "- 修正 distfeeds.list：只保留官方 7 条源。原版 FeedSourcesAppendAPK 会把\n"
               "  passwall_luci / passwall_packages / luci_app_easytier / immortalwrt_luci /\n"
               "  immortalwrt_packages 这 5 个第三方 feed 也写成 downloads.openwrt.org 上的路径，\n"
               "  而官方站只有 7 个目录 → apk update 必然报错。\n"
               "- 新增 apk-selfrepo 包（编译时生成）：把本次编译发布的 8 条自建源写进固件。\n"
               "- 新增「发布自建 apk 源」步骤：把 bin/packages/<arch>/* 与\n"
               "  bin/targets/<t>/packages（kmod-* 与 kernel/base-files/libc 等 nonshared 包）\n"
               "  发成 pkgs-<run>-* Release；保留最近 5 次，旧的自动清理。\n"
               "  注意本固件未开 CONFIG_BUILDBOT，不存在 bin/targets/<t>/kmods/。\n"
               "- 新增最后一步「校验自建源可达性」：匿名拉取 8 条 URL，任一非 200 即标红。\n"
               "- 「发布自建 apk 源」加 continue-on-error：它在「上传固件」之前，硬失败会因\n"
               "  隐含 success() 把 213MB 固件产物一起跳过；改为只标红该步，把关交给最后一步。\n"
               "- 上传 glob 补 *.ubi / *.itb / *.tar.gz / bin/packages/**；\n"
               "  日志 artifact 打开 include-hidden-files（否则 .config 不会被收集）。\n"
               "\n"
               "【精简】\n"
               "- sing-box → sing-box-tiny（PROVIDES:=sing-box，CONFLICTS:=sing-box），\n"
               "  同时关掉 PassWall 的无条件 select：INCLUDE_SingBox=n。\n"
               "- 去掉 xray-core（省 10.75 MB）：唯一开关是 INCLUDE_Xray=n，\n"
               "  因为 select PACKAGE_xray-core 是原生 Kconfig select，手写 =n 会被 defconfig 翻回。\n"
               "- 新增 luci-app-pushbot（克隆到 package/，根目录即包目录不能当 feed）。\n"
               "- 新增 bind-host（官方 packages feed 的 net/bind 子包）。\n"
               "\n"
               "【校验】\n"
               "- REQUIRED 增加 apk-selfrepo / openwrt-keyring 等；\n"
               "- FORBIDDEN 增加 sing-box full / xray-core 及其 INCLUDE_* 开关。",
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
