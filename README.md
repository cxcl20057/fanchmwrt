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
| `xray-core`（**刻意不编译**） | OpenWrt 官方 feed `net/xray-core` | 从 `.config` **排除** | 它不在 PassWall 的 `LUCI_DEPENDS` 里，只由 `INCLUDE_Xray` 段里一句原生 Kconfig `select PACKAGE_xray-core` 引入 —— 该 select 优先级高于用户设置，只能在这个开关处关掉。PassWall 改用 sing-box 核心（运行时探测、且 sing-box 优先） |
| `bind-host` | **OpenWrt 官方 feed** `net/bind` | 直接 `.config` 勾选 | `net/bind` 拆出的最小子包，提供 `/usr/bin/host`；`bind-libs` 由依赖带入 |
| `luci-app-pushbot` | zzsj0928/luci-app-pushbot `master` | **克隆进 `package/`** 当本地包（非 feed） | 仓库是「根目录即包目录」结构，Makefile 在仓库根，**当不了 feed**；纯 ucode，要求 LuCI ≥ 23.05 |
| `apk-selfrepo`（**自建 apk 源清单**） | 编译时由 workflow 现生成到 `package/apk-selfrepo/` | 本地包，不在 `.git` 里 | 只做一件事：往 `/etc/apk/repositories.d/` 放 `99-selfrepo.list`，把「本次编译发布的 8 个源」写进固件。详见**第九节** |

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
6. **生成 `apk-selfrepo` 包**（把本次编译要发布的 8 条自建源写进固件）
7. `feeds update -a`
8. **官方 feed 逐个 `install -a`** + **第三方 feed 白名单 install**
9. 去重保险：删掉第三方与官方重名的软链
10. 可选套用 `feeds_patches/luci`
11. **修正 `distfeeds.list` 为显式 7 条官方源**（去掉必然 404 的第三方 feed 行）
12. `make defconfig` + **关键包校验（缺一个就 fail）+ 互斥校验（sing-box full / xray-core 均不得为 y）**
13. `make download` → `make -j`
14. **发布自建 apk 源**：把 `bin/packages/<arch>/*` 与 `bin/targets/<t>/packages` 发成 `pkgs-<run>-*` Release，并清理旧 run
15. 上传日志与固件 artifact
16. **校验 8 条自建源可达**（匿名拉取，任一非 200 就标红）

---

## 四、使用步骤

```bash
# 1. 把上面这些文件按目录结构放进仓库并提交
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

主要插件体积（**apk 压缩后实测值**，取自 `downloads.openwrt.org` 的 `Content-Length`，aarch64_cortex-a53）：

| 组件 | 实测体积 |
|---|---|
| ~~`xray-core`~~ | **不编译** —— 本可占 **10.75 MB**，现已省下 |
| `sing-box-tiny`（passwall 与 homeproxy 共用） | **13.62 MB**（full 版 **18.09 MB**，换 tiny 只回收 **4.47 MB**，并非早前估的 10 MB） |
| `geoview` + 数据 | 4–6 MB |
| `shadowsocks-rust` + `shadowsocksr-libev` + `simple-obfs` + `v2ray-plugin` | 6–8 MB |
| `haproxy` | **1.69 MB** |
| `easytier`（含 web 控制台） | 10–15 MB |
| `frps` / `vlmcsd` / `rtp2httpd` | 各 2–5 MB |
| `bind-host` + `bind-libs` | **1.24 MB**（0.04 + 1.20，动态库占大头） |
| `luci-app-pushbot`（`jq`/`curl`/`iputils-arping` 依赖另计） | < 1 MB（纯脚本） |
| LuCI + 中文语言包 | 8–10 MB |

参照：run #5（含 `xray-core` + sing-box **full**）的 `sysupgrade.bin` = **55.85 MB**。
本轮「去 xray + 换 tiny」两笔合计预计回收 **约 15 MB**，产出落在 **41–43 MB** 区间。

若刷完仍觉紧张，优先关这几项（改 `config/ax3000t-stock.config` 后重跑）：

- `CONFIG_EASYTIER_INCLUDE_WEBCONSOLE=n`（easytier-web 是最大头）
- `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Geoview=n`
- `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Shadowsocks_Rust_Client=n`

`v2ray-geodata` 默认已关闭（解包 25–30 MB）。

---

## 七、已知风险与排查

| 现象 | 原因 | 处理 |
|---|---|---|
| PassWall 面板能开但代理不通 | 防火墙走的是 `firewall4`/nftables | 确认配置里 `luci-app-passwall_Nftables_Transparent_Proxy=y`，且 `kmod-nft-tproxy`、`kmod-nft-socket` 已编译进去（配置已显式固定） |
| HomeProxy 启动报 sing-box 配置错误 | fanchmwrt 把 packages feed 锁在 `f91b06b3`（2026-05-13），该提交里 sing-box 是 **1.12.17**；immortalwrt 25.12.2 用的是 1.12.25 —— 同属 1.12.x 线，配置结构兼容 | 若真命中，改用 `box` 之外的 core 或临时把 `CONFIG_PACKAGE_sing-box-tiny=n` + `INCLUDE_SingBox=y` 回到 full 版 |
| 组装阶段报 `sing-box` 与 `sing-box-tiny` 冲突 | 两者带 `CONFLICTS`，同时进固件必然爆 | 校验步骤已把 `CONFIG_PACKAGE_sing-box` 列为**必须未启用**，会在编译前拦下 |
| PassWall 里节点跑不起来、日志提示找不到核心 | 本方案刻意 `INCLUDE_Xray=n`，固件内无 `/usr/bin/xray`；若节点是 **XHTTP** 等 Xray 独有传输，sing-box 无法承载 | 先确认 `/usr/bin/sing-box` 存在（`which sing-box`）——存在则 PassWall 会用它；把该节点换成 sing-box 支持的类型（vmess/vless/trojan/ss/tuic/hysteria2/REALITY）；确需 XHTTP 就把 `INCLUDE_Xray` 改回 `y` 重编 |
| `luci-app-pushbot` 菜单不出现 / 页面空白 | 它是纯 ucode 架构（无 Lua/CBI），依赖 LuCI ≥ 23.05 的 ucode 控制器；另外它需要 `jq`、`curl`、`iputils-arping` | 确认 `CONFIG_PACKAGE_luci-app-pushbot=y`（依赖已自动带入）；若 kernel 日志显示 ucode 报错，说明该 LuCI 分支过旧 |
| 手动装**上游 Release** 的 pushbot apk 报 `ucode-2026.01.16~85922056-r1: breaks: luci-app-pushbot-6.00-r28[ucode>=2026.02.27]` | 上游 CI 用 **luci master** 编译：master 的 `luci.mk` 有 `LUCI_UT_MIN_UCODE?=2026.02.27`，且 `CONFIG_LUCI_UTMIN` 默认 `y`，会把 `ucode/template/*.ut` 预编译成 **format 0x02 字节码**并声明 ucode 下限；本固件 ucode 是 `2026.01.16~85922056`（fanchmwrt 锁的 luci 是 **openwrt-25.12 分支 `e9ebca7`**，里面既没有 UTMIN 也没有这条下限） | **不要装上游 Release 的 apk**（强装也会因字节码格式被拒而白屏）。pushbot 已内置于本方案固件；确需单独补装，就用同一次编译产出的 `luci-app-pushbot-*.apk`（产物里 `bin/packages/<arch>/{base,luci}/`），同树编译 ⇒ 无此约束 |
| `feeds install` 阶段报 `No feed for package 'xxx'` | 依赖名与预期不符（上游改了包名） | 到日志里搜该包名，在 `diy-part1.sh` 后单独补一条白名单安装 |
| 编译到一半 OOM | `-j5` 在 16GB runner 上跑满 | 把 `make -j$(( $(nproc) + 1 ))` 改成 `make -j2` |
| 打不开 `luci-app-rtp2httpd` 页面但装上了 | 只装了 LuCI 没装守护进程 | 配置里已同时勾了 `rtp2httpd`，校验步骤会拦；若手动改动请一并保留 |
| 刷完后 `apk update` 报某条源取不到（`Not Found` / `temporary error`） | 原版 fanchmwrt 用 `FeedSourcesAppendAPK` 把 **所有** feed 都写成 `downloads.openwrt.org/.../<feed>/packages.adb`，其中 `passwall_luci`、`passwall_packages`、`luci_app_easytier`、`immortalwrt_luci`、`immortalwrt_packages` 这 5 个第三方 feed 在官方下载站上**根本不存在**（它只发布 7 个目录），必然 404 | 本方案已修：`distfeeds.list` 只保留官方 7 条；第三方 feed / 本地包 / kmod 改由**本次编译自己发布成 Release** 并由 `apk-selfrepo` 预置。见第九节 |
| 装 kmod 报 `cannot satisfy dependency` / vermagic 不符 | 内核 vermagic 与官方不同（本固件 `6.12.87~f6c834707c435c09f2147f1e1358ba32`，官方 `6.12.87-1-82967b4996cac5f682958cca092c9ab1`），官方源的 `kmod-*` 一个都装不上 | 用自建源（第九节）里的 kmod；**自建源只包含本次编译选中并编出来的那些 kmod**，没编的（如 `kmod-usb-net-rndis`）任何源都装不了，只能改 `.config` 重编 |
| `apk add` 报 `UNTRUSTED signature` | 自建源用**每次编译新生成**的密钥签名，跨 run 混用必然验不过 | 只能用同一 run 的自建源（固件里已按 run 号固化）；不要手工把别的 run 的源加进来 |
| 刷机后想回官方小米固件 | stock 布局未动 U-Boot | 直接走小米官方恢复流程即可 |

---

## 八、常见改动指引

**加插件**：先看它的 Makefile 在哪一层 —— 这决定引入方式。
- 官方 feed 里有 → 直接在 `config/ax3000t-stock.config` 加 `CONFIG_PACKAGE_xxx=y`
- 第三方仓库，Makefile 在 `<包名>/`（子目录）→ `diy-part1.sh` 加 `src-git`，再白名单 `feeds install -p <feed> <包名>`
- 第三方仓库，多个包散在子目录里（如 immortalwrt/luci 的 `applications/*`）→ sparse-checkout 后以 `src-link` 挂载
- **单包仓库且 Makefile 就在仓库根**（如 `luci-app-pushbot`）→ **不能当 feed**：`scripts/feeds` 的 crawl 只下探一层找 `<feed>/<包名>/Makefile`，仓库根的 Makefile 永远扫不到。此时直接 `git clone <url> package/<包名>` 当本地包，这也是这类插件的通用做法。

> 判断技巧：feed 根目录下必须有 `包名/Makefile` 这一层。`luci-app-pushbot` 仓库根就是包目录 → 走 `package/` 克隆；`immortalwrt/luci` 的包在 `applications/` 下 → 可以当 feed。

**别装上游 Release 的预编译 apk（ucode 版本雷区）**：LuCI 插件的版本约束**不在它自己的 Makefile 里**，
而是 `luci.mk` 按条件自动加的 —— 三个条件同时成立就加 `ucode (>=<LUCI_UT_MIN_UCODE>)`：

| 条件 | 本仓库实际值 |
|---|---|
| `${CURDIR}/ucode/template` 目录存在（该插件有 ucode 模板） | pushbot 有：`ucode/template/pushbot/pushbot_status.ut` |
| `CONFIG_LUCI_UTMIN=y`（预编译 ucode 模板，**官方默认 y**） | 25.12 的 luci.mk 里**没有这个特性**，天然不成立 |
| `luci.mk` 里有 `LUCI_UT_MIN_UCODE` 这行（**只有 luci master 有**） | fanchmwrt 锁的 `e9ebca7`（openwrt-25.12）**没有** |

`UtMin` 用 **host 侧 ucode** 执行 `ucode -c` 把 `.ut` 编译成字节码；字节码首字节是格式号，
**ucode 运行时要求格式号完全相等，不等就拒执行**。格式 `0x02` 是 ucode **2026-02-27** 引入的，
所以 master 分支编出的 pushbot apk 会写死 `ucode(>=2026.02.27)`；本固件 ucode 是 `2026.01.16` ⇒ 装不上，
而且**绕过校验强行装也没用**（模板是 0x02 字节码，页面必然报 ucode 错）。

判断一个 apk 能不能装（不用真装）：
```sh
apk add --simulate --allow-untrusted ./luci-app-xxx.apk   # 一眼看出有没有 ucode(>=YYYY.MM.DD) 约束
```

**换 sing-box 变体**：`config` 里 `CONFIG_PACKAGE_sing-box-tiny=y` 与 `# CONFIG_PACKAGE_sing-box is not set` 必须成对出现；
且 `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_SingBox` 必须为 `n`（它是无条件 `select PACKAGE_sing-box`，开着的另一支会把 full 版硬拉回来）。
HomeProxy 走的是虚拟依赖 `+sing-box`，tiny 因为 `PROVIDES:=sing-box` 能自动顶替，无需改它。

**不编译 `xray-core`（省 10.75 MB）**：把 `config/ax3000t-stock.config` 里
`CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Xray` 设为 `n`。

- **必须在源头关**：见 `luci-app-passwall/Makefile` 的 `define Package/…/config` 段 ——
  ```
  config PACKAGE_luci-app-passwall_INCLUDE_Xray
      bool "Include Xray"
      select PACKAGE_xray-core
  ```
  这是**原生 Kconfig select**，优先级高于用户在 `.config` 里的设置。所以
  手写 `CONFIG_PACKAGE_xray-core=n` 无效（`make defconfig` 会翻回 `y`），
  唯一开关就是这里。（`xray-core` 并不在 `LUCI_DEPENDS` 里，不是硬依赖。）
- **PassWall 不会因此瘫掉**：核心探测是**运行时**做的 ——
  `SINGBOX_BIN=$(first_type $(config_n_get @global_app[0] sing_box_file) sing-box)`、
  `XRAY_BIN=$(first_type $(config_n_get @global_app[0] xray_file) xray)`；
  而所有自动选择点（`app.sh` 第 94 / 423 / 515 / 553 / 1472 行）一律
  「**先 sing-box，后 xray**」。固件里只要有 `/usr/bin/sing-box` 就够用。
- **代价**：Xray 独有协议不可用 —— 典型代表是 **XHTTP** 传输；
  `DNS_MODE` 设为 `xray` 时会自动退化到 sing-box。
  其余 vmess / vless / trojan / ss / tuic / hysteria2 / REALITY 由 sing-box 承载，不受影响。
- **加回来**：把该行改回 `y`（会连带装回 `xray-core` 10.75 MB），其余配置不用动。
- 流水线的确认手段：互斥校验把 `CONFIG_PACKAGE_xray-core`、
  `CONFIG_PACKAGE_luci-app-passwall_INCLUDE_Xray` 列为**必须未启用**；
  编译完成后「显示产物」还会 grep 固件 manifest，出现 `xray-core` 就直接 fail。

**切成大分区（OpenWrt U-Boot 布局）**：把 `config/ax3000t-stock.config` 里设备名改成
`xiaomi_mi-router-ax3000t-ubootmod`，并把 `CONFIG_TARGET_..._DEVICE_xiaomi_mi-router-ax3000t`
相应替换。注意大分区**必须先刷 U-Boot**，且与原厂布局的固件不通用。

**不带 PassWall/HomeProxy 的精简固件**：把 `config` 里对应块设为 `=n` 即可，
两个代理插件彼此独立，可以只留一个。

---

## 九、让固件能「随便装源里的软件」（apk 源机制）

### 9.1 两层源

| 层 | 谁生成 | 条数 | 内容 |
|---|---|---|---|
| **官方源** | `base-files` 的包安装脚本（本方案已改成显式 7 条） | 7 | 官方 25.12.4：`targets/<t>/packages` + `packages/<arch>/{base,packages,luci,routing,telephony,video}` |
| **自建源** | 编译时现生成的 `apk-selfrepo` 包 + workflow 的「发布自建 apk 源」步骤 | 8 | 官方站上**没有**的那些：`kmod-*`、`nonshared` 包、本地包、第三方 feed 包 |

落到固件里的两个文件：

- `/etc/apk/repositories.d/distfeeds.list` —— 官方源
- `/etc/apk/repositories.d/99-selfrepo.list` —— 自建源（`apk-selfrepo` 包安装）

### 9.2 为什么必须有自建源（三条源码级事实）

1. **官方站只有 7 个目录**。`targets/<board>/<subtarget>/packages` 与 `packages/<arch>/` 下的 `base packages luci routing telephony video`。第三方 feed 的 URL（如 `packages/<arch>/passwall_luci/packages.adb`）一律 404，而 `apk update` 遇到取不到的源会报错。原版 fanchmwrt 只 `sed -i '/fanchmwrt/d'` 掉了自己那一行，5 条第三方 feed 全留着。
2. **`kmod-*` 强绑内核 vermagic**。本固件 `6.12.87~f6c834707c435c09f2147f1e1358ba32`，官方 `6.12.87-1-82967b4996cac5f682958cca092c9ab1` —— 官方源的 kmod 一个都装不上。
3. **`nonshared` 包只在本树编得出来**。`include/package-dumpinfo.mk`：
   ```makefile
   $(if $(filter nonshared,$(PKGFLAGS)),,Repository: $(if $(FEED),$(FEED),base))
   ```
   `PKGFLAGS` 含 `nonshared` 时**不写** `Repository:` → 该包没有 `subdir` → `FeedPackageDir`（`include/feeds.mk`）回落到 `$(PACKAGE_DIR)`，即 `bin/targets/<board>/<subtarget>/packages`（`rules.mk:180`：`PACKAGE_DIR?=$(BIN_DIR)/packages`）。**`kmod-*` 与 `kernel` / `base-files` / `libc` 都在这个目录里。**

> ⚠️ 本固件**不存在** `bin/targets/<t>/kmods/`。那个目录是 `CONFIG_BUILDBOT` 专属 —— `include/feeds.mk` 里
> `$(if $(CONFIG_BUILDBOT), echo '%U/targets/%S/kmods/$(LINUX_VERSION)-$(LINUX_RELEASE)-$(LINUX_VERMAGIC)/packages.adb';)`
> 我们没开 buildbot，所以 kmod 与其它 nonshared 包同处 `targets/<t>/packages`。把源写成 `.../kmods/packages.adb` 会 404。

### 9.3 8 条自建源

URL 形式：`https://github.com/<owner>/<repo>/releases/download/pkgs-<run>-<name>/packages.adb`

| Release tag | 对应源目录 | 里面是什么 |
|---|---|---|
| `pkgs-<run>-target` | `bin/targets/<board>/<subtarget>/packages/` | `kmod-*`（约 100 个）+ `kernel`、`base-files`、`libc`、`libgcc1`、`mtd`、`ubi-utils`、`uboot-envtools`、`fstools`、`dropbear` 等 |
| `pkgs-<run>-base` | `bin/packages/<arch>/base/` | 本地包（`apk-selfrepo` 自身、`luci-app-pushbot`、`fwxd`、`libfwx_common`）+ core 树里非 nonshared 的包 |
| `pkgs-<run>-fanchmwrt` | `bin/packages/<arch>/fanchmwrt/` | fanchmwrt-packages feed：`luci-app-fwx-*` ×17 + `luci-i18n-fwx-*-zh-cn` ×17 |
| `pkgs-<run>-passwall_luci` | `bin/packages/<arch>/passwall_luci/` | `luci-app-passwall` |
| `pkgs-<run>-passwall_packages` | `bin/packages/<arch>/passwall_packages/` | `geoview`、`ipt2socks`、`simple-obfs`、`v2ray-plugin`、`shadowsocks-rust`、`shadowsocksr-libev` |
| `pkgs-<run>-luci_app_easytier` | `bin/packages/<arch>/luci_app_easytier/` | `luci-app-easytier`、`easytier` |
| `pkgs-<run>-immortalwrt_luci` | `bin/packages/<arch>/immortalwrt_luci/` | `luci-app-vlmcsd`、`luci-app-rtp2httpd`、`luci-app-homeproxy` |
| `pkgs-<run>-immortalwrt_packages` | `bin/packages/<arch>/immortalwrt_packages/` | `vlmcsd`、`rtp2httpd` |

（`luci` / `packages` / `routing` / `telephony` / `video` 这 5 个官方 feed **不需要**自建：`feeds.buildinfo` 与本仓库 `feeds.conf.default` 的 5 个 pin 逐字相同，版本天然对齐，直接用官方源即可。）

### 9.4 签名 —— 所以不需要 `--allow-untrusted`

| 环节 | 事实 |
|---|---|
| 索引签名 | 本配置 `CONFIG_SIGNED_PACKAGES=y`；`package/Makefile` 生成时带 `--sign $(BUILD_KEY_APK_SEC)`，**`packages.adb` 自带签名** |
| 密钥来源 | `rules.mk`：`BUILD_KEY_APK_SEC=$(TOPDIR)/private-key.pem`、`BUILD_KEY_APK_PUB=$(TOPDIR)/public-key.pem`；workflow 不缓存它们 ⇒ **每次编译换一把钥匙** |
| 固件信任 | 非 buildbot 构建时 `base-files/install-key` 把 `public-key.pem` 装进 `/etc/apk/keys/`（apk 分支） |
| 结论 | 同一 run 的固件 ⇄ 自建源互信，`apk add` 不用加 `--allow-untrusted`；**跨 run 混用必然报 `UNTRUSTED signature`** |

### 9.5 维护与限制

- 每次编译自动发布，**保留最近 5 次 run**（`KEEP_N=5`），更早的 Release 自动删除。每个 run 的包约 60~70 MB。
- ⇒ **旧固件的自建源会被清掉**（URL 里带 run 号）。要长期保留就把「清理旧源」整段注释掉。
- 编译结束有一步「**校验自建源可达性**」：逐条**匿名**拉取 8 个 URL，任一非 200 就把这次运行标红 —— 避免出现「固件刷好了、源却是 404」。
- 「显示产物」步骤会打印 `bin/packages/<arch>/*/` 各目录的 apk 数与索引状态，以及 `bin/targets/<t>/packages/` 的 kmod 数，用来确认每条源都真有内容。

### 9.6 在路由器上验证

```sh
cat /etc/apk/repositories.d/distfeeds.list    # 应恰好 7 条，全是 downloads.openwrt.org
cat /etc/apk/repositories.d/99-selfrepo.list  # 8 条 + 注释，指向本仓库 pkgs-<run>-*
apk update                                    # 不应出现任何 Not Found / temporary error
apk search pushbot                            # 能搜到自建源里的包
apk add --simulate luci-app-fwx-dashboard     # 预演安装
```

首次使用建议先 `apk update` 拉一次索引。
