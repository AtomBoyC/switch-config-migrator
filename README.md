<div align="center">

# 🔀 SwitchMigrator · 交换机配置迁移工具

**更换汇聚交换机时，通过 Console 线把旧交换机配置一键导入新交换机，支持跨品牌自动转换**

华三 H3C ⇄ 锐捷 Ruijie ⇄ 华为 Huawei ⇄ 思科 Cisco

[![Release](https://img.shields.io/github/v/release/AtomBoyC/switch-config-migrator?label=下载最新版)](https://github.com/AtomBoyC/switch-config-migrator/releases/latest)
![Platform](https://img.shields.io/badge/平台-Windows%20%7C%20macOS-blue)
![Python](https://img.shields.io/badge/Python-3.9%2B-green)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

</div>

---

## 📖 简介

在高校、企业机房做汇聚交换机替换时，传统做法是：用 SecureCRT 连旧交换机 → 导出配置 → 对照着在新交换机上一条条手敲，**换了品牌还得逐条翻译命令、逐个核对端口号**，费时且容易出错。

SwitchMigrator 把这件事变成 5 步点按钮：

```
 旧交换机 ──Console──▶ 自动读取配置 ──▶ 解析 ──▶ 端口映射 ──▶ 生成新品牌命令 ──Console──▶ 一键下发到新交换机
```

- 🔌 **直接走 Console 串口**：不需要旧设备有网络、不需要 TFTP/FTP
- 🔄 **跨品牌转换**：华三、华为、锐捷、思科任意互转（例如 H3C S5560 → 锐捷 S5750）
- 🧭 **智能端口映射**：自动读取新设备真实端口，按"同速率、同位置"对应，可手动调整
- ✅ **逐条下发 + 报错检测**：每条命令检查设备返回，失败的命令标红并生成报告
- 💾 **自动备份**：读取旧配置时自动保存一份备份
- 🖥️ **免安装**：提供 Windows 单文件 exe 和 macOS App，下载即用

## 📥 下载

前往 **[Releases 页面](https://github.com/AtomBoyC/switch-config-migrator/releases/latest)** 下载：

| 系统 | 文件 | 说明 |
|---|---|---|
| Windows 10/11 (64位) | `SwitchMigrator-Windows-exe.zip` | 解压后双击 `SwitchMigrator.exe` |
| macOS (Intel 芯片) | `SwitchMigrator-macOS-intel.zip` | 解压后双击 `SwitchMigrator.app` |
| macOS (Apple 芯片) | `SwitchMigrator-macOS-arm64.zip` | M1/M2/M3/M4，解压后双击 `SwitchMigrator.app` |

> **macOS 首次打开**提示"无法验证开发者"时：右键 App → 打开 → 打开（只需一次）。
> 使用前需安装 USB 转串口驱动（CH340 / PL2303 / FTDI），并关闭 SecureCRT 等占用串口的软件。

## 🚀 快速上手

| 步骤 | 操作 |
|:-:|---|
| **①** | Console 线接**旧交换机** → 选串口 → 点「连接并读取旧交换机配置」（也可导入 CRT 导出的配置文件） |
| **②** | Console 线换到**新交换机** → 点「连接新交换机并读取端口列表」 |
| **③** | 检查自动生成的端口映射，双击可修改；红色行表示"有配置但未映射" |
| **④** | 预览生成的新设备命令，**核对右侧“需人工处理”的项目** |
| **⑤** | 点「开始一键下发」，完成后自动保存配置并生成下发报告 |

详细图文说明见 👉 **[使用手册](docs/USER_GUIDE.md)**

## 🧩 支持转换的配置

| 类别 | 内容 |
|---|---|
| 基础 | 主机名、VLAN（含名称/描述）、STP 模式与优先级 |
| 二层端口 | 描述、shutdown、access / trunk（允许 VLAN、PVID）、hybrid（自动转换）、STP 边缘端口 |
| 链路聚合 | Bridge-Aggregation / Eth-Trunk / AggregatePort / Port-channel，静态与 LACP |
| 三层 | VLAN 接口 IP（含从地址）、三层路由口、Loopback、静态路由、OSPF |
| 网关冗余 | VRRP（虚拟 IP、优先级、抢占延时） |
| 服务 | DHCP 中继、DHCP Snooping 及信任口、SNMP 团体字/位置/联系人、NTP |

**不会自动转换**（工具会列出原文，提示人工处理）：账号密码/AAA（密文无法跨厂商转换）、ACL、QoS、MSTP 域、VPN 实例、IPv6、其他路由协议等。

各厂商命令对照详见 👉 **[支持的命令对照表](docs/SUPPORTED_COMMANDS.md)**

## ⚠️ 安全提示

- 本工具只向**新交换机**写入配置，对旧交换机只执行只读命令（`display` / `show`）和关闭分页等会话级命令。
- 下发前会弹窗确认，并检测当前连接设备的品牌是否与目标配置一致，防止接错设备。
- **割接前请务必人工核对**上联聚合口、网关 VRRP、DHCP 中继等关键配置。建议先在新设备上完成下发和核对，再上架替换。
- 不同型号/软件版本支持的命令存在差异，个别命令失败属于正常情况，请参考下发报告手动补配。

## 🛠️ 从源码运行

```bash
git clone https://github.com/AtomBoyC/switch-config-migrator.git
cd switch-config-migrator
pip install -r requirements.txt
python main.py
```

命令行离线转换（不连设备）：

```bash
python main.py --cli samples/h3c_s5560_sample.cfg --to ruijie -o new.txt
```

自行打包：Windows 运行 `build_windows.bat`，macOS 运行 `build_mac.sh`。
架构说明、如何新增厂商、测试方法见 👉 **[开发文档](docs/DEVELOPMENT.md)**

## 🤝 参与贡献

遇到**无法识别的配置**或**下发失败的命令**，欢迎 [提交 Issue](https://github.com/AtomBoyC/switch-config-migrator/issues/new/choose)，附上（脱敏后的）配置片段、设备型号和软件版本，我会尽快适配。也欢迎直接提交 Pull Request。

## 📄 许可证

[MIT License](LICENSE) © 2026 AtomBoyC

> 本工具按"原样"提供，使用者应自行评估在生产网络中使用的风险。
