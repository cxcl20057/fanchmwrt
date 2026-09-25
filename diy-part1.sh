#!/bin/bash
# ============================================================
# diy-part1.sh —— 在 fanchmwrt 源码根目录执行
#
# 作用：向 feeds.conf.default 追加编译所需插件的额外 feed。
#
# ★ 关键点：feed 的顺序决定了「重名包取哪一个」。
#   scripts/feeds 的依赖解析函数 lookup_package() 会先看当前包所属 feed，
#   然后按 feeds.conf 自上而下找第一个提供该包名的 feed。
#   因此第三方 feed 必须【追加在最后】，这样：
#     - xray-core / sing-box / microsocks / haproxy / libcurl …… 取 OpenWrt 官方版本
#     - chinadns-ng / dns2socks / tcping / geoview …… 官方没有，才回落到 PassWall 包源
#
# ★ 不整仓挂载 immortalwrt/luci 与 immortalwrt/packages：
#   它们是 openwrt 同名 feed 的 fork，包名高度重合，会顶掉官方版本，
#   并让 fanchmwrt 对 luci feed 打的补丁（feeds_patches/luci）失效。
#   ImmortalWrt 的几个独有包改由 scripts/prepare-immortalwrt-feeds.sh
#   稀疏拉取后以 src-link 方式挂载。
#
# 环境变量：
#   IMM_SRC  指向上一步稀疏克隆的父目录（内含 luci/ 与 packages/）
# ============================================================
set -e

SRC_DIR="${1:-.}"
cd "$SRC_DIR"

: "${IMM_SRC:?请先设置 IMM_SRC 指向 immortalwrt 稀疏克隆目录}"

echo "==> 原始 feeds.conf.default"
cat feeds.conf.default

[ -f feeds.conf.default.bak ] || cp feeds.conf.default feeds.conf.default.bak

# 幂等：每次都从备份重新生成，避免重复追加
cp -f feeds.conf.default.bak feeds.conf.default

cat >> feeds.conf.default <<EOF

# ===== 第三方插件 feed（务必置于官方 feed 之后，保证重名包取官方版本）=====

# PassWall —— LuCI 界面（仓库内只有 luci-app-passwall 一个包）
src-git passwall_luci https://github.com/Openwrt-Passwall/openwrt-passwall.git;main

# PassWall —— 代理内核与依赖（chinadns-ng / dns2socks / tcping / geoview / ipt2socks /
#            simple-obfs / shadowsocksr-libev / v2ray-plugin / shadow-tls / naiveproxy …）
#            与官方 feed 重名的包会被「官方优先」规则自动跳过
src-git passwall_packages https://github.com/Openwrt-Passwall/openwrt-passwall-packages.git;main

# EasyTier —— 提供 luci-app-easytier 与 easytier（含 easytier-web 控制台）
src-git luci_app_easytier https://github.com/EasyTier/luci-app-easytier.git;main

# ImmortalWrt 25.12 精选项（src-link 指向稀疏克隆，只含需要的包）
src-link immortalwrt_luci      $IMM_SRC/luci
src-link immortalwrt_packages  $IMM_SRC/packages
EOF

echo
echo "==> 生成后的 feeds.conf.default"
cat feeds.conf.default
