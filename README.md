# fanchmwrt · 小米 AX3000T（stock 分区）GitHub Actions 编译方案

## 一句话结论

以 `fanchmwrt/fanchmwrt@fanchmwrt-25.12.4` 为基线，**用 OpenWrt 官方 feed 满足绝大部分插件**，
只对官方 feed 里确实没有的包引入第三方源；immortalwrt 采用 **稀疏拉取 + 白名单安装**，
不整仓挂载，从而避开重名包冲突和对 luci feed 补丁的破坏。设备固定为
`mediatek/filogic → xiaomi_mi-router-ax3000t`（stock 布局）。

---

## 二、插件来源矩阵（已逐一核实）

| 插件 | 实际来源 | feed / 引入方式 | 说明 |
|---|---|---|---|
| `luci-app-passwall` | Openwrt-Passwall/openwrt-passwall `main` | `src-git passwall_luci` 白名单安装 | 只有 `main` 一个分支 |
| PassWall 依赖（`chinadns-ng`/`dns2socks`/`tcping`/`geoview`/`ipt2socks`/`simple-obfs`/`shadowsocksr-libev`/`v2ray-plugin`…） | Openwrt-Passwall/openwrt-passwall-packages `main` | `src-git passwall_packages`，由依赖自动装入 | 与官方重名的包（`xray-core`/`sing-box`/`microsocks`）自动回落到官方版本 |
| `luci-app-homeproxy` | **ImmortalWrt 25.12** `applications/luci-app-homeproxy` | 稀疏拉取 + `src-link` | 独立仓库 `immortalwrt/homeproxy` 的 Makefile 在**仓库根目录**，`scripts/feeds` 扫描用 `-mindepth 1`，无法当 feed 用，必须从 luci feed 取 |
| `luci-app-easytier` + `easytier` | EasyTier/luci-app-easytier `main` | `src-git luci_app_easytier` 白名单安装 | `easytier` 包会从 GitHub Release 下载预编译 aarch64 二进制（v2.6.4） |
| `luci-app-rtp2httpd` + `rtp2httpd` | **ImmortalWrt 25.12** | 稀疏拉取 + `src-link` | LuCI 在 `applications/`，守护进程在 `net/` |
| `luci-app-vlmcsd` + `vlmcsd` | **ImmortalWrt 25.12** | 稀疏拉取 + `src-link` | 同上 |
| `luci-app-frps` + `frps` | **OpenWrt 官方 feed**（不用 immortalwrt） | 直接 `.config` 勾选 | 官方 luci/packages feed 已自带，版本与源码树同年同代，更稳 |
| `ddns-scripts-dnspod` | **OpenWrt 官方 feed** | 直接 `.config` 勾选 | 官方 `net/ddns-scripts` 已含 `ddns-scripts-dnspod`、`-dnspod-v3` |
| `openssh-sftp-server` | **核心源码树** `package/network/services/openssh` | 直接 `.config` 勾选 | 无需任何 feed |
| `sing-box-tiny`（**替换** `sing-box` full 版） | **OpenWrt 官方 feed** `net/sing-box` | 直接 `.config` 勾选 | 同一个 Makefile 产出 `sing-box`(full, `DEFAULT_VARIANT`) 与 `sing-box-tiny`；tiny 带 `PROVIDES:=sing-box` + `CONFLICTS:=sing-box`，装的是同一个 `/usr/bin/sing-box`，可顶替 |
| `bind-host` | **OpenWrt 官方 feed** `net/bind` | 直接 `.config` 勾选 | `net/bind` 拆出的最小子包，提供 `/usr/bin/host`；`bind-libs` 由依赖带入 |
| `luci-app-pushbot` | zzsj0928/luci-app-pushbot `master` | **克隆进 `package/`** 当本地包（非 feed） | 仓库是「根目录即包目录」结构，Makefile 在仓库根，**当不了 feed**；纯 ucode，要求 LuCI ≥ 23.05 |

> 你原话提到「immortalwrt 25.12.2 里有以上插件」——核实结果：**immortalwrt 25.12.2
> 的包仓库确实是 `.apk` 格式（aarch64_cortex-a53）**，上述插件在里面全部存在。
> 但直接拿它的 `.apk` 灌进 fanchmwrt 固件会踩内核版本不一致的坑（`kmod-*` 强绑定），
> 所以这里全部**从源码编译**，`kmod-nft-tproxy` / `kmod-tun` 等才会与内核严格配套。

---

## 三、仓库文件与作用

放到你的仓库 `cxcl20057/fanchmwrt` 根目录：

```
.github/workflows/build.yml             编译流水线
diy-part1.sh                            向 feeds.conf.default 追加第三方 feed
scripts/prepare-immortalwrt-feeds.sh    稀疏拉取 immortalwrt 的 4 个包
scripts/push-to-github.py               一键建仓 + 推送 + 触发（本地用，不参与编译）
config/ax3000t-stock.config             增量配置（目标设备 + 插件清单）
```

**触发方式**：推到 `main` 分支会自动开跑（`on.push.paths` 命中 workflow / config / diy / scripts）。
也可以在 Actions 页面点 **Run workflow** 手动触发，并选择是否开启 SSH 失败调试。

流水线执行顺序：

1. 释放磁盘空间（OpenWrt 全量编译要 ~25GB，runner 默认不够）
2. 浅克隆 fanchmwrt 源码
3. 稀疏拉取 immortalwrt 扩展包 → `$WORKSPACE/immortalwrt-src/{luci,packages}`
4. `diy-part1.sh` 追加 feed
5. **克隆 `luci-app-pushbot` 到 `package/`**（本地包，非 feed）
6. `feeds update -a`
7. **官方 feed 逐个 `install -a`** + **第三方 feed 白名单 install**
8. 去重保险：删掉第三方与官方重名的软链
9. 可选套用 `feeds_patches/luci`
10. `make defconfig` + **关键包校验（缺一个就 fail）+ 互斥校验（sing-box full 不得为 y）**
11. `make download` → `make -j` → 上传 artifact

---

## 四、使用步骤

```bash
# 1. 把 4 个文件按上面的目录结构放进仓库并提交
git add -A && git commit -m "add AX3000T build pipeline" && git push

# 2. GitHub 仓库 → Actions 标签页 → 允许 workflow（首次需要手动 Enable）

# 3. 左侧选「编译 fanchmwrt (Xiaomi AX3000T · stock)」→ Run workflow
```

跑完在 Actions 运行的 Artifacts 里下载 `fanchmwrt-ax3000t-stock-<run号>`，
其中 `*sysupgrade.bin` 就是刷机包（stock 布局，无需先刷 U-Boot）。

---

## 五、三个关键设计决策（这是最容易踩坑的地方）

### 1. 为什么第三方 feed 必须追加在**最后**？

`scripts/feeds` 的依赖解析函数 `lookup_package()` 会先看当前包所属 feed，
再**按 `feeds.conf` 自上而下**取第一个提供该包名的 feed。

`openwrt-passwall-packages` 里同时包含 `xray-core`、`sing-box`、`microsocks`。
把官方 feed 放前面，`passwall` 需要的这些包就会取官方版本；
只有官方没有的（`chinadns-ng`、`dns2socks`、`tcping`、`geoview`…）才回落到 passwall 包源。

### 2. 为什么**不能**对第三方 feed 用 `feeds install -a`？

`feeds install -a` 会把该 feed 的每个包都软链到 `package/feeds/<feed>/`。
两个 feed 各自链接同名包 → 构建时元数据出现**重名 Package**，
取到哪一个由扫描顺序（字母序）决定，不可控。
所以官方 feed 走 `-a -p <feed>`，第三方 feed 一律**点名安装**。

### 3. 为什么**不能**把 immortalwrt/luci 整仓加成 feed？

- 它是 `openwrt/luci` 的 fork，`luci-base` / `luci-mod-network` 等几乎 1:1 重名，
  会把官方版本顶掉；
- fanchmwrt 对 luci feed 打了本地补丁（`feeds_patches/luci`），
  该机制只作用于 `feeds/luci`，换 feed 会让补丁**静默失效**。

因此 immortalwrt 只贡献 4 个官方没有的包，用 `git sparse-checkout` 秒级拉取，
再以 `src-link`（本地软链 feed）挂载。注意 sparse-checkout 的 cone 模式会连同
仓库根的 `luci.mk` 一起检出——这几个 luci 包内部用的是 `include ../../luci.mk`，
**缺了它直接编译失败**。

---

## 六、分区与体积预算

AX3000T stock 布局（来自 `target/linux/mediatek/dts/mt7981b-xiaomi-mi-router-ax3000t.dts`）：

| 分区 | 大小 | 承载内容 |
|---|---|---|
| `ubi_kernel` | `0x2200000` = **34 MiB** | kernel |
| `ubi` | `0x4e00000` = **78 MiB** | rootfs + rootfs_data（可用空间的实际上限） |

主要插件体积估算：

| 组件 | 约占用 |
|---|---|
| `xray-core` | 8–12 MB |
| `sing-box-tiny`（passwall 与 homeproxy 共用一份） | 8–10 MB（full 版是 18–22 MB，**换 tiny 回收约 10 MB**） |
| `geoview` + 数据 | 4–6 MB |
| `shadowsocks-rust` + `shadowsocksr-libev` + `simple-obfs` + `v2ray-plugin` | 6–8 MB |
| `haproxy` | 1–2 MB |
| `easytier`（含 web 控制台） | 10–15 MB |
| `frps` / `vlmcsd` / `rtp2httpd` | 各 2–5 MB |
| `bind-host` + `bind-libs` | 1.5–2 MB（动态库占大头，`host` 本身几十 KB） |
| `luci-app-pushbot`（`jq`/`curl`/`iputils-arping` 依赖另计） | < 1 MB（纯脚本） |
| LuCI + 中文语言包 | 8–10 MB |

合计约 **50–60 MB**，落在 78 MiB 里。
若刷完发现剩余空间紧张，优先关掉这几项（改 `config/ax3000t-stock.config` 后重跑）：

- `CONFIG_EASYTIER_INCLUDE_WEBCONSOLE=n`（easytier-web 是最大头）
- `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Geoview=n`
- `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Shadowsocks_Rust_Client=n`

`v2ray-geodata` 默认已关闭（解包 25–30MB）。

---

## 七、已知风险与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| PassWall 面板能开但代理不通 | 防火墙走的是 `firewall4`/nftables | 确认配置里 `luci-app-passwall_Nftables_Transparent_Proxy=y`，且 `kmod-nft-tproxy`、`kmod-nft-socket` 已编译进去（配置已显式固定） |
| HomeProxy 启动报 sing-box 配置错误 | fanchmwrt 把 packages feed 锁在 `f91b06b3`（2026-05-13），该提交里 sing-box 是 **1.12.17**；immortalwrt 25.12.2 用的是 1.12.25 —— 同属 1.12.x 线，配置结构兼容 | 若真命中，改用 `box` 之外的 core 或临时把 `CONFIG_PACKAGE_sing-box-tiny=n` + `INCLUDE_SingBox=y` 回到 full 版 |
| 组装阶段报 `sing-box` 与 `sing-box-tiny` 冲突 | 两者带 `CONFLICTS`，同时进固件必然爆 | 校验步骤已把 `CONFIG_PACKAGE_sing-box` 列为**必须未启用**，会在编译前 5 分钟内拦下 |
| `luci-app-pushbot` 菜单不出现 / 页面空白 | 它是纯 ucode 架构（无 Lua/CBI），依赖 LuCI ≥ 23.05 的 ucode 控制器；另外它需要 `jq`、`curl`、`iputils-arping` | 确认 `CONFIG_PACKAGE_luci-app-pushbot=y`（依赖已自动带入）；若 kernel 日志显示 ucode 报错，说明该 LuCI 分支过旧 |
| `feeds install` 阶段报 `No feed for package 'xxx'` | 依赖名与预期不符（上游改了包名） | 到日志里搜该包名，在 `diy-part1.sh` 后单独补一条白名单安装 |
| 编译到一半 OOM | `-j5` 在 16GB runner 上跑满 | 把 `make -j$(( $(nproc) + 1 ))` 改成 `make -j2` |
| 打不开 `luci-app-rtp2httpd` 页面但装上了 | 只装了 LuCI 没装守护进程 | 配置里已同时勾了 `rtp2httpd`，校验步骤会拦；若手动改动请一并保留 |
| 刷机后想回官方小米固件 | stock 布局未动 U-Boot | 直接走小米官方恢复流程即可 |

---

## 八、常见改动指引

**加插件**：先看它的 Makefile 在哪一层 —— 这决定引入方式。
- 官方 feed 里有 → 直接在 `config/ax3000t-stock.config` 加 `CONFIG_PACKAGE_xxx=y`
- 第三方仓库，Makefile 在 `<包名>/`（子目录）→ `diy-part1.sh` 加 `src-git`，再白名单 `feeds install -p <feed> <包名>`
- 第三方仓库，多个包散在子目录里（如 immortalwrt/luci 的 `applications/*`）→ sparse-checkout 后以 `src-link` 挂载
- **单包仓库且 Makefile 就在仓库根**（如 `luci-app-pushbot`）→ **不能当 feed**：`scripts/feeds` 的 crawl 只下探一层找 `<feed>/<包名>/Makefile`，仓库根的 Makefile 永远扫不到。此时直接 `git clone <url> package/<包名>` 当本地包，这也是这类插件的通用做法。

> 判断技巧：feed 根目录下必须有 `包名/Makefile` 这一层。`luci-app-pushbot` 仓库根就是包目录 → 走 `package/` 克隆；`immortalwrt/luci` 的包在 `applications/` 下 → 可以当 feed。

**换 sing-box 变体**：`config` 里 `CONFIG_PACKAGE_sing-box-tiny=y` 与 `# CONFIG_PACKAGE_sing-box is not set` 必须成对出现；
且 `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_SingBox` 必须为 `n`（它是无条件 `select PACKAGE_sing-box`，开着的另一支会把 full 版硬拉回来）。
HomeProxy 走的是虚拟依赖 `+sing-box`，tiny 因为 `PROVIDES:=sing-box` 能自动顶替，无需改它。

**切成大分区（OpenWrt U-Boot 布局）**：把 `config/ax3000t-stock.config` 里设备名改成
`xiaomi_mi-router-ax3000t-ubootmod`，并把 `CONFIG_TARGET_..._DEVICE_xiaomi_mi-router-ax3000t`
相应替换。注意大分区**必须先刷 U-Boot**，且与原厂布局的固件不通用。

**不带 PassWall/HomeProxy 的精简固件**：把 `config` 里对应块设为 `=n` 即可，
两个代理插件彼此独立，可以只留一个。
