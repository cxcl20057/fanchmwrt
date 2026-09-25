#!/bin/bash
# ============================================================
# prepare-immortalwrt-feeds.sh
#
# 作用：用 git sparse-checkout 精确拉取 ImmortalWrt 25.12 中「OpenWrt 官方
#       feed 里没有」的少数几个包，交给 feeds 以 src-link 形式挂载。
#
# 为什么不直接把 immortalwrt/luci 和 immortalwrt/packages 整仓加成 feed？
#   - 这两个仓库是 openwrt/luci、openwrt/packages 的 fork，包名几乎 1:1 重合
#     （luci-base、luci-mod-network、dnsmasq、libcurl……）。
#     整仓挂载 + feeds install -a 会产生大量重名包，构建时取到哪一个不确定，
#     且会顶掉 fanchmwrt 对 luci feed 打的补丁（feeds_patches/luci 只对
#     feeds/luci 生效）。
#   - 所以这里只取出真正需要的 4 个包 + luci.mk，其余一律用官方 feed。
#
# 用法： prepare-immortalwrt-feeds.sh <目标目录>
#        生成 <目标目录>/luci 与 <目标目录>/packages 两个稀疏克隆
# ============================================================
set -euo pipefail

DEST="${1:?用法: $0 <目标目录>}"
BRANCH="openwrt-25.12"   # 与 fanchmwrt 25.12.4 同代，勿用 master

mkdir -p "$DEST"

# clone_sparse <仓库URL> <目标目录> <要检出的路径...>
clone_sparse() {
  local url="$1" dir="$2"; shift 2
  echo "==> 稀疏拉取 $url ($BRANCH)"
  rm -rf "$dir"
  # --filter=blob:none 只拉取需要的 blob，几秒即可完成，避免整仓数百 MB
  git clone --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$url" "$dir"
  # cone 模式会自动把仓库根目录下的文件（如 luci.mk）一并检出
  git -C "$dir" sparse-checkout set "$@"
  echo "    已检出：$(git -C "$dir" sparse-checkout list | tr '\n' ' ')"
}

# LuCI 应用：KMS 服务端 / RTP→HTTP 组播转单播 / HomeProxy
# 注意：仓库根目录的 luci.mk 必须存在，因为包内用的是 include ../../luci.mk
clone_sparse "https://github.com/immortalwrt/luci.git" "$DEST/luci" \
  applications/luci-app-vlmcsd \
  applications/luci-app-rtp2httpd \
  applications/luci-app-homeproxy

# 对应的守护进程 / 可执行文件包
clone_sparse "https://github.com/immortalwrt/packages.git" "$DEST/packages" \
  net/vlmcsd \
  net/rtp2httpd

echo
echo "==> 完成。目录结构："
find "$DEST" -maxdepth 4 -name Makefile -printf '    %p\n' | sort
