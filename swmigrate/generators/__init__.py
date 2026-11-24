"""根据中间模型生成各厂商配置命令。

输出为带缩进的命令行列表，可直接逐行下发（下发时会去掉行首空格）。
"""
from ..util import area_str, to_wildcard, vlan_ranges, vlan_str_dash, vlan_str_to


def generate(cfg, vendor):
    if vendor in ("h3c", "huawei"):
        return _gen_comware(cfg, vendor)
    if vendor in ("ruijie", "cisco"):
        return _gen_ios(cfg, vendor)
    raise ValueError(vendor)


def _sorted_ports(cfg):
    from ..ifname import sort_key
    return sorted(cfg.interfaces.values(), key=lambda i: sort_key(i.name))


def _sanitize_name(s, maxlen=31):
    s = s.strip().replace('"', "").replace("?", "")
    return s[:maxlen]


# ------------------------------------------------------------------ 华三 / 华为
def _gen_comware(cfg, v):
    hw = v == "huawei"
    L = []
    q = "quit"
    if cfg.hostname:
        L.append(f"sysname {cfg.hostname}")

    vlans = sorted(x for x in cfg.vlans if 1 < x < 4095)
    if vlans:
        if hw:
            for chunk in vlan_str_to(vlans):
                L.append(f"vlan batch {chunk}")
        else:
            for a, b in vlan_ranges(vlans):
                L.append(f"vlan {a}" if a == b else f"vlan {a} to {b}")
        for vid in vlans:
            name = cfg.vlans[vid]
            if name:
                L += [f"vlan {vid}", f" name {_sanitize_name(name)}", q]

    if cfg.stp_mode:
        L.append(f"stp mode {cfg.stp_mode}")
    if cfg.stp_priority is not None:
        if cfg.stp_mode == "mstp" or (not hw and cfg.stp_mode is None):
            L.append(f"stp instance 0 priority {cfg.stp_priority}")
        else:
            L.append(f"stp priority {cfg.stp_priority}")
    if cfg.stp_enabled is True:
        L.append("stp enable" if hw else "stp global enable")
    elif cfg.stp_enabled is False:
        L.append("stp disable" if hw else "undo stp global enable")

    if any(s.dhcp_relay for s in cfg.svis.values()) or cfg.dhcp_snooping:
        L.append("dhcp enable")
    if cfg.dhcp_snooping:
        L.append("dhcp snooping enable")

    def l2(itf):
        out = []
        if itf.mode == "access":
            out.append(" port link-type access")
            if itf.access_vlan and itf.access_vlan != 1:
                out.append(f" port default vlan {itf.access_vlan}" if hw
                           else f" port access vlan {itf.access_vlan}")
        elif itf.mode == "trunk":
            out.append(" port link-type trunk")
            allowed = itf.allowed if itf.allowed is not None else {1}
            kw = "allow-pass" if hw else "permit"
            if allowed >= set(range(2, 4095)):
                out.append(f" port trunk {kw} vlan all")
            else:
                if 1 not in allowed:
                    out.append(f" undo port trunk {kw} vlan 1")
                rest = sorted(x for x in allowed if x != 1)
                for chunk in vlan_str_to(rest):
                    out.append(f" port trunk {kw} vlan {chunk}")
            if itf.native_vlan and itf.native_vlan != 1:
                out.append(f" port trunk pvid vlan {itf.native_vlan}")
        return out

    def common_head(itf):
        out = [f"interface {itf.name}"]
        if itf.description:
            out.append(f" description {itf.description}")
        return out

    def common_tail(itf):
        out = []
        if itf.stp_edge:
            out.append(" stp edged-port enable" if hw else " stp edged-port")
        if itf.name in cfg.dhcp_snooping_trusted:
            out.append(" dhcp snooping trusted" if hw else " dhcp snooping trust")
        out.append(" shutdown" if itf.shutdown else " undo shutdown")
        out.append(q)
        return out

    # 聚合口
    for lid in sorted(cfg.lags):
        lag = cfg.lags[lid]
        L += common_head(lag)
        if lag.lag_mode in ("active", "passive"):
            L.append(" mode lacp" if hw else " link-aggregation mode dynamic")
        L += l2(lag)
        L += common_tail(lag)

    # 物理口
    for itf in _sorted_ports(cfg):
        if not itf.has_config:
            continue
        L += common_head(itf)
        if itf.lag_id:
            L.append(f" eth-trunk {itf.lag_id}" if hw else f" port link-aggregation group {itf.lag_id}")
            if hw:
                pass
            if not hw and cfg.lags.get(itf.lag_id) and cfg.lags[itf.lag_id].lag_mode == "passive":
                L.append(" lacp mode passive")
        elif itf.mode == "routed":
            L.append(" undo portswitch" if hw else " port link-mode route")
            for ip, mask, sec in itf.ips:
                L.append(f" ip address {ip} {mask}" + (" sub" if sec else ""))
        else:
            L += l2(itf)
        L += common_tail(itf)

    # VLAN 接口
    for vid in sorted(cfg.svis):
        s = cfg.svis[vid]
        L.append(f"interface {'Vlanif' if hw else 'Vlan-interface'}{vid}")
        if s.description:
            L.append(f" description {s.description}")
        for ip, mask, sec in s.ips:
            L.append(f" ip address {ip} {mask}" + (" sub" if sec else ""))
        if s.dhcp_relay:
            L.append(" dhcp select relay")
            for ip in s.dhcp_relay:
                L.append(f" dhcp relay server-ip {ip}" if hw else f" dhcp relay server-address {ip}")
        for vr in s.vrrp.values():
            for vip in vr.vips:
                L.append(f" vrrp vrid {vr.vrid} virtual-ip {vip}")
            if vr.priority is not None:
                L.append(f" vrrp vrid {vr.vrid} priority {vr.priority}")
            if vr.preempt_delay is not None:
                L.append(f" vrrp vrid {vr.vrid} preempt-mode timer delay {vr.preempt_delay}" if hw
                         else f" vrrp vrid {vr.vrid} preempt-mode delay {vr.preempt_delay}")
        if s.shutdown:
            L.append(" shutdown")
        L.append(q)

    for num in sorted(cfg.loopbacks):
        lb = cfg.loopbacks[num]
        L.append(f"interface LoopBack{num}")
        if lb.description:
            L.append(f" description {lb.description}")
        for ip, mask, sec in lb.ips:
            L.append(f" ip address {ip} {mask}" + (" sub" if sec else ""))
        L.append(q)

    for rt in cfg.routes:
        parts = ["ip route-static", rt.dest, rt.mask]
        if rt.interface:
            parts.append("NULL0" if rt.interface.upper() == "NULL0" else rt.interface)
        if rt.nexthop:
            parts.append(rt.nexthop)
        if rt.preference is not None:
            parts += ["preference", str(rt.preference)]
        if rt.description:
            parts += ["description", rt.description.replace(" ", "_")]
        L.append(" ".join(parts))

    for o in cfg.ospf:
        L.append(f"ospf {o.pid}" + (f" router-id {o.router_id}" if o.router_id else ""))
        if o.default_originate:
            L.append(" default-route-advertise")
        if o.import_direct:
            L.append(" import-route direct")
        if o.import_static:
            L.append(" import-route static")
        for s in o.silent:
            L.append(f" silent-interface {s}")
        areas = {}
        for net, wc, area in o.networks:
            areas.setdefault(area_str(area, "dotted"), []).append((net, to_wildcard(wc)))
        for area, nets in areas.items():
            L.append(f" area {area}")
            for net, wc in nets:
                L.append(f"  network {net} {wc}")
            L.append(f" {q}")
        L.append(q)

    if cfg.snmp_communities or cfg.snmp_location or cfg.snmp_contact:
        L.append("snmp-agent")
        L.append("snmp-agent sys-info version v2c" if hw else "snmp-agent sys-info version v1 v2c")
        for name, rw in cfg.snmp_communities:
            L.append(f"snmp-agent community {'write' if rw == 'rw' else 'read'} {name}")
        if cfg.snmp_location:
            L.append(f"snmp-agent sys-info location {cfg.snmp_location}")
        if cfg.snmp_contact:
            L.append(f"snmp-agent sys-info contact {cfg.snmp_contact}")

    if cfg.ntp_servers:
        if not hw:
            L.append("ntp-service enable")
        for ip in cfg.ntp_servers:
            L.append(f"ntp-service unicast-server {ip}")
    return L


# ------------------------------------------------------------------ 锐捷 / 思科
def _gen_ios(cfg, v):
    rj = v == "ruijie"
    L = []
    q = "exit"
    if cfg.hostname:
        L.append(f"hostname {cfg.hostname}")

    vlans = sorted(x for x in cfg.vlans if 1 < x < 4095)
    named = {x for x in vlans if cfg.vlans[x]}
    unnamed = [x for x in vlans if x not in named]
    if unnamed:
        if rj:
            # 锐捷: vlan range 批量创建
            for a, b in vlan_ranges(unnamed):
                if a == b:
                    L += [f"vlan {a}", q]
                else:
                    L += [f"vlan range {a}-{b}", q]
        else:
            for chunk_start in range(0, len(unnamed), 50):
                L += [f"vlan {vlan_str_dash(unnamed[chunk_start:chunk_start + 50])}", q]
    for vid in sorted(named):
        L += [f"vlan {vid}", f" name {_sanitize_name(cfg.vlans[vid]).replace(' ', '_')}", q]

    stp_mode = cfg.stp_mode
    if cfg.stp_enabled is not False and (stp_mode or cfg.stp_enabled or cfg.stp_priority is not None):
        if rj:
            L.append("spanning-tree")
            if stp_mode:
                L.append(f"spanning-tree mode {stp_mode}")
            if cfg.stp_priority is not None:
                L.append(f"spanning-tree mst 0 priority {cfg.stp_priority}" if stp_mode == "mstp"
                         else f"spanning-tree priority {cfg.stp_priority}")
        else:
            if stp_mode:
                L.append("spanning-tree mode " + {"rstp": "rapid-pvst", "stp": "pvst", "mstp": "mst"}
                         .get(stp_mode, stp_mode))
            if cfg.stp_priority is not None:
                L.append(f"spanning-tree mst 0 priority {cfg.stp_priority}" if stp_mode == "mstp"
                         else f"spanning-tree vlan 1-4094 priority {cfg.stp_priority}")
    elif cfg.stp_enabled is False:
        L.append("no spanning-tree")

    if rj and any(s.dhcp_relay for s in cfg.svis.values()):
        L.append("service dhcp")
    if cfg.dhcp_snooping:
        L.append("ip dhcp snooping")

    def l2(itf):
        out = []
        if itf.mode == "access":
            out.append(" switchport mode access")
            if itf.access_vlan and itf.access_vlan != 1:
                out.append(f" switchport access vlan {itf.access_vlan}")
        elif itf.mode == "trunk":
            out.append(" switchport mode trunk")
            if itf.native_vlan and itf.native_vlan != 1:
                out.append(f" switchport trunk native vlan {itf.native_vlan}")
            if itf.allowed is not None:
                rngs = vlan_ranges(itf.allowed)
                chunks = [rngs[i:i + 30] for i in range(0, len(rngs), 30)] or [[]]
                for i, ch in enumerate(chunks):
                    s = ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ch)
                    if rj:
                        op = "only" if i == 0 else "add"
                        out.append(f" switchport trunk allowed vlan {op} {s}" if s
                                   else " switchport trunk allowed vlan none")
                    else:
                        op = "" if i == 0 else "add "
                        out.append(f" switchport trunk allowed vlan {op}{s}" if s
                                   else " switchport trunk allowed vlan none")
        return out

    def head(itf):
        out = [f"interface {itf.name}"]
        if itf.description:
            out.append(f" description {itf.description}")
        return out

    def tail(itf):
        out = []
        if itf.stp_edge:
            out.append(" spanning-tree portfast")
        if itf.name in cfg.dhcp_snooping_trusted:
            out.append(" ip dhcp snooping trust")
        out.append(" shutdown" if itf.shutdown else " no shutdown")
        out.append(q)
        return out

    # 成员口先加入聚合组（锐捷会自动创建 AggregatePort）
    ports = _sorted_ports(cfg)
    for itf in ports:
        if not itf.lag_id:
            continue
        lag = cfg.lags.get(itf.lag_id)
        mode = lag.lag_mode if lag else "on"
        L += head(itf)
        if rj:
            L.append(f" port-group {itf.lag_id}" + (f" mode {mode}" if mode in ("active", "passive") else ""))
        else:
            L.append(f" channel-group {itf.lag_id} mode {mode if mode in ('active', 'passive') else 'on'}")
        L += tail(itf)

    for lid in sorted(cfg.lags):
        lag = cfg.lags[lid]
        L += head(lag)
        L += l2(lag)
        L += tail(lag)

    for itf in ports:
        if itf.lag_id or not itf.has_config:
            continue
        L += head(itf)
        if itf.mode == "routed":
            L.append(" no switchport")
            for ip, mask, sec in itf.ips:
                L.append(f" ip address {ip} {mask}" + (" secondary" if sec else ""))
        else:
            L += l2(itf)
        L += tail(itf)

    for vid in sorted(cfg.svis):
        s = cfg.svis[vid]
        L.append(f"interface {'VLAN ' if rj else 'Vlan'}{vid}")
        if s.description:
            L.append(f" description {s.description}")
        for ip, mask, sec in s.ips:
            L.append(f" ip address {ip} {mask}" + (" secondary" if sec else ""))
        for ip in s.dhcp_relay:
            L.append(f" ip helper-address {ip}")
        for vr in s.vrrp.values():
            for i, vip in enumerate(vr.vips):
                L.append(f" vrrp {vr.vrid} ip {vip}" + (" secondary" if i and not rj else ""))
            if vr.priority is not None:
                L.append(f" vrrp {vr.vrid} priority {vr.priority}")
            if vr.preempt_delay is not None:
                L.append(f" vrrp {vr.vrid} preempt delay {vr.preempt_delay}" if rj
                         else f" vrrp {vr.vrid} preempt delay minimum {vr.preempt_delay}")
        L.append(" shutdown" if s.shutdown else " no shutdown")
        L.append(q)

    for num in sorted(cfg.loopbacks):
        lb = cfg.loopbacks[num]
        L.append(f"interface {'Loopback ' if rj else 'Loopback'}{num}")
        if lb.description:
            L.append(f" description {lb.description}")
        for ip, mask, sec in lb.ips:
            L.append(f" ip address {ip} {mask}" + (" secondary" if sec else ""))
        L.append(q)

    for rt in cfg.routes:
        parts = ["ip route", rt.dest, rt.mask]
        if rt.interface:
            parts.append("Null 0" if rt.interface.upper() == "NULL0" and rj
                         else ("Null0" if rt.interface.upper() == "NULL0" else rt.interface))
        if rt.nexthop:
            parts.append(rt.nexthop)
        if rt.preference is not None:
            parts.append(str(rt.preference))
        if rt.description:
            parts += ["description" if rj else "name", rt.description.replace(" ", "_")]
        L.append(" ".join(parts))

    for o in cfg.ospf:
        L.append(f"router ospf {o.pid}")
        if o.router_id:
            L.append(f" router-id {o.router_id}")
        for net, wc, area in o.networks:
            L.append(f" network {net} {to_wildcard(wc)} area {area_str(area, 'int')}")
        if o.import_direct:
            L.append(" redistribute connected subnets")
        if o.import_static:
            L.append(" redistribute static subnets")
        if o.default_originate:
            L.append(" default-information originate")
        for s in o.silent:
            L.append(f" passive-interface {s}")
        L.append(q)

    for name, rw in cfg.snmp_communities:
        L.append(f"snmp-server community {name} {rw}")
    if cfg.snmp_location:
        L.append(f"snmp-server location {cfg.snmp_location}")
    if cfg.snmp_contact:
        L.append(f"snmp-server contact {cfg.snmp_contact}")
    for ip in cfg.ntp_servers:
        L.append(f"ntp server {ip}")
    return L
