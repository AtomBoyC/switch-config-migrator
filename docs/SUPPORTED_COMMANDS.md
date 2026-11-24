# 支持的命令对照表

工具先把源配置解析为**与厂商无关的中间模型**，再生成目标厂商的命令。下表列出各项在四个厂商下的写法（即本工具能识别和生成的语法）。

## 接口命名

| 类型 | 华三 H3C | 华为 Huawei | 锐捷 Ruijie | 思科 Cisco |
|---|---|---|---|---|
| 千兆 | `GigabitEthernet1/0/1` | `GigabitEthernet0/0/1` | `GigabitEthernet 0/1` | `GigabitEthernet1/0/1` |
| 万兆 | `Ten-GigabitEthernet1/0/49` | `XGigabitEthernet0/0/1` | `TenGigabitEthernet 0/25` | `TenGigabitEthernet1/1/1` |
| 25G | `Twenty-FiveGigE1/0/1` | `25GE1/0/1` | `TFGigabitEthernet 0/1` | `TwentyFiveGigE1/0/1` |
| 40G | `FortyGigE1/0/53` | `40GE1/0/1` | `FortyGigabitEthernet 0/1` | `FortyGigabitEthernet1/0/1` |
| 100G | `HundredGigE1/0/1` | `100GE1/0/1` | `HundredGigabitEthernet 0/1` | `HundredGigE1/0/1` |
| 聚合口 | `Bridge-Aggregation1` | `Eth-Trunk1` | `AggregatePort 1` | `Port-channel1` |
| VLAN 接口 | `Vlan-interface10` | `Vlanif10` | `VLAN 10` | `Vlan10` |

缩写同样能识别：`GE1/0/1`、`XGE1/0/49`、`BAGG1`、`Gi1/0/1`、`Te1/1/1`、`Po1` 等。

## VLAN

| 功能 | 华三 | 华为 | 锐捷 | 思科 |
|---|---|---|---|---|
| 批量创建 | `vlan 2 to 10` | `vlan batch 2 to 10` | `vlan range 2-10` | `vlan 2-10` |
| 名称 | `vlan 10` / `name X`（或 `description X`） | `vlan 10` / `name X`（或 `description X`） | `vlan 10` / `name X` | `vlan 10` / `name X` |

## 二层端口

| 功能 | 华三 | 华为 | 锐捷 | 思科 |
|---|---|---|---|---|
| Access | `port link-type access`<br>`port access vlan 10` | `port link-type access`<br>`port default vlan 10` | `switchport mode access`<br>`switchport access vlan 10` | 同锐捷 |
| Trunk | `port link-type trunk` | `port link-type trunk` | `switchport mode trunk` | 同锐捷 |
| 允许 VLAN | `port trunk permit vlan 10 20 to 30`<br>`undo port trunk permit vlan 1` | `port trunk allow-pass vlan …`<br>`undo port trunk allow-pass vlan 1` | `switchport trunk allowed vlan only 10,20-30`<br>（也识别 add/remove/except） | `switchport trunk allowed vlan 10,20-30` |
| PVID/Native | `port trunk pvid vlan 10` | `port trunk pvid vlan 10` | `switchport trunk native vlan 10` | 同锐捷 |
| Hybrid | `port hybrid vlan 10 untagged`<br>`port hybrid pvid vlan 10` | `port hybrid untagged vlan 10` | 转换为 access/trunk | 转换为 access/trunk |
| 边缘端口 | `stp edged-port` | `stp edged-port enable` | `spanning-tree portfast` | `spanning-tree portfast` |
| 三层口 | `port link-mode route` | `undo portswitch` | `no switchport` | `no switchport` |

> **Trunk 默认允许 VLAN 的差异**：华三/华为 trunk 默认只允许 VLAN 1；锐捷/思科默认允许全部 VLAN。工具会按各自默认值换算，生成精确的允许列表。

## 链路聚合

| 功能 | 华三 | 华为 | 锐捷 | 思科 |
|---|---|---|---|---|
| 成员口加入 | `port link-aggregation group 1` | `eth-trunk 1` | `port-group 1 [mode active]` | `channel-group 1 mode active\|on` |
| LACP | 聚合口下 `link-aggregation mode dynamic` | 聚合口下 `mode lacp` | 成员口 `mode active/passive` | 成员口 `mode active/passive` |

生成顺序已按各厂商要求处理（例如锐捷先成员口加入 port-group，再配置 AggregatePort；华为先配 Eth-Trunk 再加成员）。

## 三层与路由

| 功能 | 华三 | 华为 | 锐捷 | 思科 |
|---|---|---|---|---|
| 从 IP | `ip address x m sub` | `ip address x m sub` | `ip address x m secondary` | 同锐捷 |
| DHCP 中继 | `dhcp select relay`<br>`dhcp relay server-address X`（V5 的 server-group 也支持） | `dhcp select relay`<br>`dhcp relay server-ip X` | `service dhcp` + `ip helper-address X` | `ip helper-address X` |
| VRRP | `vrrp vrid 1 virtual-ip X`<br>`vrrp vrid 1 priority 120` | 同华三，抢占 `preempt-mode timer delay N` | `vrrp 1 ip X`<br>`vrrp 1 priority 120` | 同锐捷 |
| 静态路由 | `ip route-static D M NH [preference N]` | 同华三 | `ip route D M NH [距离]` | 同锐捷 |
| OSPF | `ospf 1 router-id X` / `area 0.0.0.0` / `network N W` | 同华三 | `router ospf 1` / `network N W area 0` | 同锐捷 |
| OSPF 引入 | `import-route direct/static` | 同华三 | `redistribute connected/static subnets` | 同锐捷 |

## 全局服务

| 功能 | 华三 | 华为 | 锐捷 | 思科 |
|---|---|---|---|---|
| STP 模式 | `stp mode rstp` | `stp mode rstp` | `spanning-tree` + `spanning-tree mode rstp` | `spanning-tree mode rapid-pvst` |
| STP 优先级 | `stp instance 0 priority 4096` | `stp priority 4096` | `spanning-tree priority 4096` | `spanning-tree vlan 1-4094 priority 4096` |
| SNMP | `snmp-agent community read X` | 同华三 | `snmp-server community X ro` | 同锐捷 |
| NTP | `ntp-service unicast-server X` | 同华三 | `ntp server X` | 同锐捷 |
| DHCP Snooping | `dhcp snooping enable` / `dhcp snooping trust` | `dhcp snooping enable` / `dhcp snooping trusted` | `ip dhcp snooping` / `ip dhcp snooping trust` | 同锐捷 |

## 不支持自动转换的内容

以下内容会在「④ 预览」页的提示区和"未转换配置"区中列出原文：

- `local-user` / `username` / AAA / RADIUS / HWTACACS / `user-interface` / `line`（密码为密文）
- ACL、QoS、traffic-policy、packet-filter
- 风暴抑制、端口隔离、环路检测、端口限速
- MSTP 域配置（`stp region-configuration`）
- VPN 实例、IPv6、BGP / RIP / ISIS 等
- 加密的 SNMP 团体字、SNMP v3 / trap
- 管理口（M-GigabitEthernet / MEth / Mgmt）IP

欢迎通过 Issue 反馈需要支持的配置项。
