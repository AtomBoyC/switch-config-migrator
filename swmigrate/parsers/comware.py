"""华三 Comware / 华为 VRP 配置解析（两者语法高度相似）。"""
import re

from ..ifname import PHYSICAL_KINDS, parse_ifname_any
from ..model import Interface, Loopback, Ospf, Route, Svi, SwitchConfig, Vrrp
from ..util import area_str, is_ip, parse_vlan_tokens, to_mask, to_wildcard
from .base import block_text, split_blocks, unquote

# 可安全忽略（新设备会自动生成或不应迁移）的顶层命令
_IGNORE_TOP = re.compile(
    r"^(clock |irf |undo irf |lldp global|password-recovery|ftth|undo copyright|"
    r"header |info-center|cpu-usage|memory|device board|undo info-center|"
    r"scheduler|transceiver|undo telnet server|telnet server enable|"
    r"ssh server|ssh user|stelnet server|undo ip http|ip http|ip https|"
    r"domain |radius scheme|hwtacacs|local-user|user-group|role name|line |"
    r"user-interface|user-group|aaa|authentication|dhcp enable|undo dhcp|"
    r"dhcp relay server-group|load xml|undo password-recovery|"
    r"snmp-agent$|snmp-agent local-engineid|snmp-agent target-host|snmp-agent trap|"
    r"snmp-agent usm-user|snmp-agent mib-view|snmp-agent group|snmp-agent sys-info version|"
    r"ntp-service enable|undo ntp-service|ntp-service source|drv|set |"
    r"system-working-mode|forward-path|vlan 1$|undo vlan 1|diagnose|patch|"
    r"license|ftp |undo ftp|sftp |http |undo http|sysname)", re.I)

# 不迁移但需要提醒用户的关键字
_NOTICE_TOP = [
    (re.compile(r"^(local-user|aaa|user-interface|line |radius|hwtacacs|domain)", re.I),
     "用户/认证/登录(local-user/AAA/user-interface/line)配置未迁移：密码为密文无法跨厂商转换，请在新设备上手动配置管理账号"),
    (re.compile(r"^snmp-agent (target-host|trap|usm-user|group|mib-view)", re.I),
     "SNMP trap/v3 配置未迁移，请手动配置"),
]


def _add_ip(lst, tokens):
    """tokens: ['10.0.0.1','255.255.255.0'('sub')]"""
    if len(tokens) < 2 or not is_ip(tokens[0]):
        return False
    sec = len(tokens) > 2 and tokens[2].lower() in ("sub", "secondary")
    lst.append((tokens[0], to_mask(tokens[1]), sec))
    return True


def parse_comware(text, vendor="h3c"):
    cfg = SwitchConfig(vendor=vendor)
    relay_groups = {}  # H3C V5: server-group id -> [ip]
    pending_relay_select = []  # (svi, group id)
    notices = set()

    for header, subs in split_blocks(text):
        toks = header.split()
        low = header.lower()
        kw = toks[0].lower()

        if kw == "sysname":
            cfg.hostname = header.split(None, 1)[1] if len(toks) > 1 else ""
            continue

        # ---------- VLAN ----------
        if kw == "vlan":
            if len(toks) > 1 and toks[1].lower() == "batch":
                for v in parse_vlan_tokens(toks[2:]):
                    cfg.vlans.setdefault(v, "")
                continue
            ids = parse_vlan_tokens(toks[1:])
            name = desc = ""
            leftovers = []
            for _, s in subs:
                st = s.split(None, 1)
                if st[0].lower() == "name" and len(st) > 1:
                    name = unquote(st[1])
                elif st[0].lower() == "description" and len(st) > 1:
                    desc = unquote(st[1])
                else:
                    leftovers.append(s)
            for v in ids:
                cfg.vlans[v] = name or desc or cfg.vlans.get(v, "")
            if leftovers:
                cfg.unparsed_blocks.append([header] + [" " + x for x in leftovers])
            continue

        if kw == "dhcp" and len(toks) >= 6 and toks[1] == "relay" and toks[2] == "server-group":
            # dhcp relay server-group 1 ip 1.1.1.1
            relay_groups.setdefault(toks[3], []).append(toks[5])
            continue

        # ---------- 接口 ----------
        if kw == "interface" and len(toks) >= 2:
            ifname = "".join(toks[1:])
            r = parse_ifname_any(ifname)
            if not r:
                cfg.unparsed_blocks.append(block_text(header, subs))
                continue
            kind, nums = r
            if kind == "VLAN":
                svi = cfg.svis.setdefault(nums[0], Svi(vlan=nums[0]))
                _parse_svi(svi, subs, vendor, relay_groups, pending_relay_select)
                continue
            if kind == "LOOP":
                lb = cfg.loopbacks.setdefault(nums[0], Loopback(num=nums[0]))
                for _, s in subs:
                    st = s.split()
                    if st[0] == "ip" and len(st) > 1 and st[1] == "address":
                        _add_ip(lb.ips, st[2:])
                    elif st[0] == "description":
                        lb.description = s.split(None, 1)[1]
                continue
            if kind in ("LAG", "RLAG") or kind in PHYSICAL_KINDS:
                itf = Interface(name=ifname, kind=kind, nums=nums)
                _parse_port(itf, subs, vendor, cfg)
                if kind in ("LAG", "RLAG"):
                    itf.lag_mode = itf.lag_mode or "on"
                    cfg.lags[nums[0]] = itf
                else:
                    cfg.interfaces[ifname] = itf
                continue
            if kind in ("NULL", "OTHER", "MGMT"):
                if kind == "MGMT" and any(s.startswith("ip address") for _, s in subs):
                    cfg.warnings.append(f"管理口 {ifname} 的 IP 地址未迁移，请按需手动配置")
                continue
            cfg.unparsed_blocks.append(block_text(header, subs))
            continue

        # ---------- 路由 ----------
        if kw == "ip" and len(toks) > 1 and toks[1] == "route-static":
            rt = _parse_route(toks[2:])
            if rt:
                cfg.routes.append(rt)
            else:
                cfg.unparsed_blocks.append([header])
            continue

        if kw == "ospf":
            cfg.ospf.append(_parse_ospf(toks, subs))
            continue

        # ---------- STP ----------
        if kw == "stp":
            rest = " ".join(toks[1:]).lower()
            if rest.startswith("mode"):
                cfg.stp_mode = toks[2].lower()
            elif rest in ("global enable", "enable"):
                cfg.stp_enabled = True
            elif rest in ("global disable", "disable"):
                cfg.stp_enabled = False
            elif re.match(r"(instance 0 )?priority \d+", rest):
                cfg.stp_priority = int(toks[-1])
            elif rest.startswith("instance") or rest.startswith("region"):
                cfg.unparsed_blocks.append(block_text(header, subs))
            elif rest.startswith("bpdu-protection"):
                cfg.unparsed_blocks.append([header])
            else:
                cfg.unparsed_blocks.append(block_text(header, subs))
            continue

        # ---------- SNMP ----------
        if kw == "snmp-agent":
            if len(toks) >= 4 and toks[1] == "community":
                # snmp-agent community read [simple|cipher] NAME [acl ..]
                # 华为: snmp-agent community read cipher %^%#...
                rw = "rw" if toks[2].lower() == "write" else "ro"
                idx = 3
                if toks[idx].lower() in ("simple", "cipher"):
                    if toks[idx].lower() == "cipher":
                        cfg.warnings.append("SNMP 团体字为密文(cipher)，无法迁移，请手动配置 SNMP 团体字")
                        continue
                    idx += 1
                if idx < len(toks):
                    cfg.snmp_communities.append((toks[idx], rw))
                continue
            if len(toks) >= 3 and toks[1] == "sys-info":
                if toks[2] == "location":
                    cfg.snmp_location = " ".join(toks[3:])
                    continue
                if toks[2] == "contact":
                    cfg.snmp_contact = " ".join(toks[3:])
                    continue
            if _IGNORE_TOP.match(header):
                notices.add("SNMP trap/v3/target-host 等配置未迁移，请手动配置")
                continue
            cfg.unparsed_blocks.append([header])
            continue

        # ---------- NTP ----------
        if kw == "ntp-service" and len(toks) >= 3 and toks[1] == "unicast-server":
            cfg.ntp_servers.append(toks[2])
            continue

        # ---------- DHCP Snooping ----------
        if low in ("dhcp snooping enable", "dhcp-snooping"):
            cfg.dhcp_snooping = True
            continue

        if low in ("dhcp enable",):
            continue

        matched_notice = False
        for rx, msg in _NOTICE_TOP:
            if rx.match(header):
                notices.add(msg)
                matched_notice = True
                break
        if matched_notice:
            continue
        if _IGNORE_TOP.match(header):
            continue

        cfg.unparsed_blocks.append(block_text(header, subs))

    # V5 dhcp relay server-select 处理
    for svi, gid in pending_relay_select:
        for ip in relay_groups.get(gid, []):
            if ip not in svi.dhcp_relay:
                svi.dhcp_relay.append(ip)

    # VLAN 接口对应的 VLAN 必须存在
    for v in cfg.svis:
        cfg.vlans.setdefault(v, "")
    for itf in cfg.interfaces.values():
        if itf.lag_id and itf.lag_id not in cfg.lags:
            cfg.lags[itf.lag_id] = Interface(name=f"LAG{itf.lag_id}", kind="LAG",
                                             nums=(itf.lag_id,), lag_mode="on")

    cfg.warnings.extend(sorted(notices))
    return cfg


def _parse_svi(svi, subs, vendor, relay_groups, pending):
    for _, s in subs:
        st = s.split()
        k = st[0].lower()
        if k == "ip" and len(st) > 2 and st[1] == "address":
            if not _add_ip(svi.ips, st[2:]):
                svi.unparsed.append(s)
        elif k == "description":
            svi.description = s.split(None, 1)[1] if len(st) > 1 else ""
        elif k == "shutdown":
            svi.shutdown = True
        elif k == "undo" and len(st) > 1 and st[1] == "shutdown":
            pass
        elif k == "dhcp":
            # H3C V7: dhcp relay server-address X
            # 华为:   dhcp relay server-ip X / dhcp relay binding server ip X
            # H3C V5: dhcp relay server-select 1
            if len(st) >= 4 and st[1] == "relay" and st[2] in ("server-address", "server-ip"):
                svi.dhcp_relay.append(st[3])
            elif len(st) >= 4 and st[1] == "relay" and st[2] == "server-select":
                pending.append((svi, st[3]))
            elif " ".join(st[1:3]) == "select relay" or " ".join(st[1:]) == "relay information enable":
                pass
            elif len(st) >= 6 and st[1:5] == ["relay", "binding", "server", "ip"]:
                svi.dhcp_relay.append(st[5])
            else:
                svi.unparsed.append(s)
        elif k == "vrrp" and len(st) >= 4 and st[1] == "vrid":
            vr = svi.vrrp.setdefault(int(st[2]), Vrrp(vrid=int(st[2])))
            sub = st[3].lower()
            if sub == "virtual-ip" and len(st) >= 5:
                vr.vips.append(st[4])
            elif sub == "priority" and len(st) >= 5:
                vr.priority = int(st[4])
            elif sub == "preempt-mode":
                if "delay" in st:
                    vr.preempt_delay = int(st[st.index("delay") + 1])
            elif sub == "preempt" or sub == "preempt-mode":
                pass
            else:
                svi.unparsed.append(s)
        elif k in ("mtu",) or (k == "undo" and len(st) > 1 and st[1] in ("ip",)):
            svi.unparsed.append(s)
        elif k in ("ipv6",):
            svi.unparsed.append(s)
        else:
            svi.unparsed.append(s)


def _parse_port(itf, subs, vendor, cfg):
    link_type = None
    trunk_allowed = None
    for _, s in subs:
        st = s.split()
        k = st[0].lower()
        sl = s.lower()
        if k == "description":
            itf.description = s.split(None, 1)[1] if len(st) > 1 else ""
        elif sl == "shutdown":
            itf.shutdown = True
        elif sl in ("undo shutdown", "port link-mode bridge", "portswitch", "combo enable fiber",
                    "combo enable copper"):
            pass
        elif sl in ("port link-mode route", "undo portswitch"):
            itf.mode = "routed"
        elif k == "port" and len(st) >= 3 and st[1] == "link-type":
            link_type = st[2].lower()
            if link_type == "trunk" and trunk_allowed is None:
                trunk_allowed = {1}
        elif k == "port" and len(st) >= 4 and st[1] == "access" and st[2] == "vlan":
            itf.access_vlan = int(st[3])
            link_type = link_type or "access"
        elif k == "port" and len(st) >= 4 and st[1] == "default" and st[2] == "vlan":  # 华为
            itf.access_vlan = int(st[3])
        elif k == "port" and len(st) >= 4 and st[1] == "trunk" and st[2] in ("permit", "allow-pass") \
                and st[3] == "vlan":
            if trunk_allowed is None:
                trunk_allowed = {1}
            trunk_allowed |= parse_vlan_tokens(st[4:])
        elif k == "undo" and len(st) >= 5 and st[1] == "port" and st[2] == "trunk" \
                and st[3] in ("permit", "allow-pass") and st[4] == "vlan":
            if trunk_allowed is None:
                trunk_allowed = {1}
            trunk_allowed -= parse_vlan_tokens(st[5:])
        elif k == "port" and len(st) >= 5 and st[1] == "trunk" and st[2] == "pvid":
            itf.native_vlan = int(st[4])
        elif k == "port" and len(st) >= 5 and st[1] == "hybrid" and st[2] == "pvid":
            itf.native_vlan = int(st[4])
        elif k == "port" and len(st) >= 4 and st[1] == "hybrid" and st[2] == "vlan":
            tag = st[-1].lower()
            vl = parse_vlan_tokens(st[3:-1])
            if tag == "untagged":
                itf.hybrid_untagged |= vl
            else:
                itf.hybrid_tagged |= vl
        elif k == "port" and len(st) >= 4 and st[1] == "hybrid" and st[2] in ("untagged", "tagged") \
                and st[3] == "vlan":  # 华为: port hybrid untagged vlan 10
            vl = parse_vlan_tokens(st[4:])
            if st[2] == "untagged":
                itf.hybrid_untagged |= vl
            else:
                itf.hybrid_tagged |= vl
        elif k == "undo" and len(st) >= 5 and st[1] == "port" and st[2] == "hybrid" and st[3] == "vlan":
            vl = parse_vlan_tokens(st[4:])
            itf.hybrid_untagged -= vl
            itf.hybrid_tagged -= vl
        elif k == "port" and len(st) >= 4 and st[1] == "link-aggregation" and st[2] == "group":
            itf.lag_id = int(st[3])
        elif k == "eth-trunk" and len(st) >= 2 and st[1].isdigit():  # 华为成员口
            itf.lag_id = int(st[1])
        elif k == "link-aggregation" and len(st) >= 3 and st[1] == "mode":
            itf.lag_mode = "active" if st[2] == "dynamic" else "on"
        elif k == "mode" and len(st) >= 2 and itf.kind in ("LAG", "RLAG"):  # 华为 Eth-Trunk
            itf.lag_mode = "active" if st[1].startswith("lacp") else "on"
        elif k == "lacp" and len(st) >= 2 and st[1] == "mode" and st[2] == "passive":
            itf.lag_mode = "passive"
        elif k == "stp" and ("edged-port" in sl):
            if "disable" not in sl:
                itf.stp_edge = True
        elif k == "ip" and len(st) > 2 and st[1] == "address":
            _add_ip(itf.ips, st[2:])
            itf.mode = "routed"
        elif k == "dhcp" and "snooping" in sl and "trust" in sl:
            cfg.dhcp_snooping_trusted.add(itf.name)
        elif k == "dhcp-snooping" and "trusted" in sl:
            cfg.dhcp_snooping_trusted.add(itf.name)
        elif k in ("broadcast-suppression", "multicast-suppression", "unicast-suppression",
                   "flow-interval", "jumboframe", "speed", "duplex", "loopback-detection",
                   "lldp", "qos", "packet-filter", "traffic-policy", "port-isolate",
                   "mac-address", "storm-control", "traffic-filter", "stp"):
            itf.unparsed.append(s)
        else:
            itf.unparsed.append(s)

    if itf.mode == "routed":
        return
    if link_type == "trunk":
        itf.mode = "trunk"
        itf.allowed = trunk_allowed if trunk_allowed is not None else {1}
    elif link_type == "access" or (link_type is None and itf.access_vlan):
        itf.mode = "access"
    elif link_type == "hybrid":
        itf.mode = "hybrid"


def _parse_route(t):
    # ip route-static [vpn-instance x] DEST MASK [IFNAME] NEXTHOP [preference N] [description X]
    if not t:
        return None
    if t[0] == "vpn-instance":
        return None
    if len(t) < 3 or not is_ip(t[0]):
        return None
    rt = Route(dest=t[0], mask=to_mask(t[1]))
    i = 2
    if i < len(t) and not is_ip(t[i]) and t[i].upper() != "NULL0":
        if parse_ifname_any(t[i]):
            rt.interface = t[i]
            i += 1
    elif i < len(t) and t[i].upper() == "NULL0":
        rt.interface = "NULL0"
        i += 1
    if i < len(t) and is_ip(t[i]):
        rt.nexthop = t[i]
        i += 1
    while i < len(t):
        if t[i] == "preference" and i + 1 < len(t):
            rt.preference = int(t[i + 1])
            i += 2
        elif t[i] == "description":
            rt.description = " ".join(t[i + 1:])
            break
        else:
            i += 1
    if not rt.nexthop and not rt.interface:
        return None
    return rt


def _parse_ospf(toks, subs):
    pid = int(toks[1]) if len(toks) > 1 and toks[1].isdigit() else 1
    o = Ospf(pid=pid)
    if "router-id" in toks:
        o.router_id = toks[toks.index("router-id") + 1]
    area = None
    for depth, s in subs:
        st = s.split()
        k = st[0].lower()
        if k == "area":
            area = area_str(st[1], "dotted")
        elif k == "network" and area is not None and len(st) >= 3:
            o.networks.append((st[1], to_wildcard(st[2]), area))
        elif k == "import-route" and len(st) >= 2:
            if st[1] == "direct":
                o.import_direct = True
            elif st[1] == "static":
                o.import_static = True
            else:
                o.unparsed.append(s)
        elif k == "default-route-advertise":
            o.default_originate = True
        elif k == "silent-interface" and len(st) >= 2:
            o.silent.append("".join(st[1:]))
        elif k == "router-id" and len(st) > 1:
            o.router_id = st[1]
        else:
            o.unparsed.append(s)
    return o
