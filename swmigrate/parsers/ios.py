"""锐捷 RGOS / 思科 IOS 配置解析。"""
import re

from ..ifname import PHYSICAL_KINDS, parse_ifname_any
from ..model import Interface, Loopback, Ospf, Route, Svi, SwitchConfig, Vrrp
from ..util import area_str, is_ip, parse_vlan_list_str, to_mask, to_wildcard
from .base import block_text, split_blocks, unquote

_IGNORE_TOP = re.compile(
    r"^(service |no service|boot |clock |enable |username |aaa |line |logging |"
    r"install |cwmp|nfpp|sn-|no cwmp|ip ssh|ip http|no ip http|ip domain|no ip domain|"
    r"snmp-server (host|enable|user|group|view|engineid)|crypto |ntp update-calendar|"
    r"ntp enable|ntp service|webmaster|web-auth|banner |version |hostname|"
    r"spanning-tree extend|vtp |errdisable|memory|archive|license|redundancy|"
    r"switch \d+ provision|system mtu|diagnostic|power |ip forward-protocol|"
    r"ip classless|control-plane|vlan internal|no ip source-route|ipv6 unicast|"
    r"ip dhcp relay information|dhcp-relay)", re.I)

_NOTICE = [
    (re.compile(r"^(username|aaa |line |enable (secret|password))", re.I),
     "用户/认证/登录(username/AAA/line/enable)配置未迁移，请在新设备上手动配置管理账号"),
    (re.compile(r"^snmp-server (host|enable|user|group|view)", re.I),
     "SNMP trap/v3 配置未迁移，请手动配置"),
]


def _add_ip(lst, st):
    if len(st) < 2 or not is_ip(st[0]):
        return False
    lst.append((st[0], to_mask(st[1]), len(st) > 2 and st[2] == "secondary"))
    return True


def parse_ios(text, vendor="ruijie"):
    cfg = SwitchConfig(vendor=vendor)
    notices = set()
    global_relay = []
    for header, subs in split_blocks(text):
        toks = header.split()
        kw = toks[0].lower()
        low = header.lower()

        if kw == "hostname" and len(toks) > 1:
            cfg.hostname = toks[1]
            continue

        if kw == "vlan" and len(toks) > 1:
            if toks[1] == "range":
                ids = parse_vlan_list_str(" ".join(toks[2:]))
            else:
                ids = parse_vlan_list_str(" ".join(toks[1:]))
            name = ""
            for _, s in subs:
                st = s.split(None, 1)
                if st[0] == "name" and len(st) > 1:
                    name = unquote(st[1])
            for v in ids:
                cfg.vlans[v] = name or cfg.vlans.get(v, "")
            continue

        if kw == "interface" and len(toks) >= 2:
            ifname = " ".join(toks[1:])
            r = parse_ifname_any(ifname)
            if not r:
                cfg.unparsed_blocks.append(block_text(header, subs))
                continue
            kind, nums = r
            if kind == "VLAN":
                svi = cfg.svis.setdefault(nums[0], Svi(vlan=nums[0]))
                _parse_svi(svi, subs)
                continue
            if kind == "LOOP":
                lb = cfg.loopbacks.setdefault(nums[0], Loopback(num=nums[0]))
                for _, s in subs:
                    st = s.split()
                    if st[:2] == ["ip", "address"]:
                        _add_ip(lb.ips, st[2:])
                    elif st[0] == "description":
                        lb.description = s.split(None, 1)[1]
                continue
            if kind in PHYSICAL_KINDS or kind in ("LAG", "RLAG"):
                itf = Interface(name=ifname, kind=kind, nums=nums)
                _parse_port(itf, subs, cfg)
                if kind in ("LAG", "RLAG"):
                    cfg.lags[nums[0]] = itf
                else:
                    cfg.interfaces[ifname] = itf
                continue
            if kind == "MGMT":
                if any(s.startswith("ip address") for _, s in subs):
                    cfg.warnings.append(f"管理口 {ifname} 的 IP 地址未迁移，请按需手动配置")
                continue
            if kind in ("NULL", "OTHER"):
                continue
            cfg.unparsed_blocks.append(block_text(header, subs))
            continue

        if kw == "ip" and len(toks) > 2 and toks[1] == "route":
            rt = _parse_route(toks[2:])
            if rt:
                cfg.routes.append(rt)
            else:
                cfg.unparsed_blocks.append([header])
            continue
        if kw == "ip" and len(toks) > 2 and toks[1] == "default-gateway":
            cfg.routes.append(Route(dest="0.0.0.0", mask="0.0.0.0", nexthop=toks[2]))
            continue
        if kw == "ip" and len(toks) > 2 and toks[1] == "helper-address":
            global_relay.append(toks[2])
            continue

        if kw == "router" and len(toks) > 1 and toks[1] == "ospf":
            cfg.ospf.append(_parse_ospf(toks, subs))
            continue

        if kw == "spanning-tree":
            rest = toks[1:]
            if not rest:
                cfg.stp_enabled = True
            elif rest[0] == "mode" and len(rest) > 1:
                cfg.stp_mode = rest[1].lower().replace("rapid-pvst", "rstp").replace("pvst", "stp")
            elif rest[0] == "priority" and len(rest) > 1:
                cfg.stp_priority = int(rest[1])
            elif rest[:2] == ["mst", "0"] and len(rest) > 3 and rest[2] == "priority":
                cfg.stp_priority = int(rest[3])
            elif rest[0] == "vlan" and "priority" in rest:
                cfg.stp_priority = int(rest[rest.index("priority") + 1])
            elif rest[0] in ("portfast",) or _IGNORE_TOP.match(header):
                pass
            else:
                cfg.unparsed_blocks.append(block_text(header, subs))
            continue
        if low == "no spanning-tree":
            cfg.stp_enabled = False
            continue

        if kw == "snmp-server" and len(toks) >= 3:
            if toks[1] == "community":
                name = toks[2]
                rw = "rw" if len(toks) > 3 and toks[3].lower() == "rw" else "ro"
                if name in ("7", "0") and len(toks) > 3:
                    if name == "7":
                        cfg.warnings.append("SNMP 团体字为加密形式，无法迁移，请手动配置")
                        continue
                    name = toks[3]
                cfg.snmp_communities.append((name, rw))
                continue
            if toks[1] == "location":
                cfg.snmp_location = " ".join(toks[2:])
                continue
            if toks[1] == "contact":
                cfg.snmp_contact = " ".join(toks[2:])
                continue

        if kw == "ntp" and len(toks) >= 3 and toks[1] == "server":
            ip = toks[-1] if is_ip(toks[-1]) else toks[2]
            cfg.ntp_servers.append(ip)
            continue

        if low == "ip dhcp snooping":
            cfg.dhcp_snooping = True
            continue

        hit = False
        for rx, msg in _NOTICE:
            if rx.match(header):
                notices.add(msg)
                hit = True
                break
        if hit or _IGNORE_TOP.match(header):
            continue
        cfg.unparsed_blocks.append(block_text(header, subs))

    for v in cfg.svis:
        cfg.vlans.setdefault(v, "")
    member_modes = getattr(cfg, "_lag_member_mode", {})
    for itf in cfg.interfaces.values():
        if itf.lag_id and itf.lag_id not in cfg.lags:
            cfg.lags[itf.lag_id] = Interface(name=f"AggregatePort {itf.lag_id}", kind="LAG",
                                             nums=(itf.lag_id,))
    for lid, lag in cfg.lags.items():
        lag.lag_mode = member_modes.get(lid, lag.lag_mode or "on")
    cfg.warnings.extend(sorted(notices))
    return cfg


def _parse_svi(svi, subs):
    for _, s in subs:
        st = s.split()
        k = st[0].lower()
        if st[:2] == ["ip", "address"]:
            if not _add_ip(svi.ips, st[2:]):
                svi.unparsed.append(s)
        elif st[:2] == ["ip", "helper-address"] and len(st) > 2:
            svi.dhcp_relay.append(st[-1])
        elif k == "description":
            svi.description = s.split(None, 1)[1] if len(st) > 1 else ""
        elif s == "shutdown":
            svi.shutdown = True
        elif s == "no shutdown":
            pass
        elif k == "vrrp" and len(st) >= 3 and st[1].isdigit():
            vr = svi.vrrp.setdefault(int(st[1]), Vrrp(vrid=int(st[1])))
            if st[2] == "ip" and len(st) > 3:
                vr.vips.append(st[3])
            elif st[2] == "priority" and len(st) > 3:
                vr.priority = int(st[3])
            elif st[2] == "preempt":
                if "delay" in st:
                    vr.preempt_delay = int(st[-1])
            else:
                svi.unparsed.append(s)
        else:
            svi.unparsed.append(s)


def _parse_port(itf, subs, cfg):
    mode = None
    allowed = None
    for _, s in subs:
        st = s.split()
        k = st[0].lower()
        if k == "description":
            itf.description = s.split(None, 1)[1] if len(st) > 1 else ""
        elif s == "shutdown":
            itf.shutdown = True
        elif s in ("no shutdown", "switchport", "medium-type fiber", "medium-type copper"):
            pass
        elif s == "no switchport":
            itf.mode = "routed"
        elif st[:2] == ["switchport", "mode"] and len(st) > 2:
            mode = st[2]
        elif st[:3] == ["switchport", "access", "vlan"]:
            itf.access_vlan = int(st[3])
        elif st[:4] == ["switchport", "trunk", "native", "vlan"]:
            itf.native_vlan = int(st[4])
        elif st[:4] == ["switchport", "trunk", "allowed", "vlan"]:
            rest = st[4:]
            if not rest:
                continue
            op = rest[0]
            if op in ("only", "add", "remove", "except", "all", "none"):
                vl = parse_vlan_list_str(" ".join(rest[1:])) if len(rest) > 1 else set()
                if op == "all":
                    allowed = None
                elif op == "none":
                    allowed = set()
                elif op == "only":
                    allowed = vl
                elif op == "add":
                    allowed = (allowed if allowed is not None else set(range(1, 4095))) | vl
                elif op == "remove":
                    allowed = (allowed if allowed is not None else set(range(1, 4095))) - vl
                elif op == "except":
                    allowed = set(range(1, 4095)) - vl
            else:
                allowed = parse_vlan_list_str(" ".join(rest))
        elif k == "port-group" and len(st) >= 2:
            itf.lag_id = int(st[1])
            if "mode" in st:
                cfg._lag_member_mode = getattr(cfg, "_lag_member_mode", {})
                cfg._lag_member_mode[int(st[1])] = st[st.index("mode") + 1]
        elif k == "channel-group" and len(st) >= 2:
            itf.lag_id = int(st[1])
            if "mode" in st:
                cfg._lag_member_mode = getattr(cfg, "_lag_member_mode", {})
                m = st[st.index("mode") + 1]
                cfg._lag_member_mode[int(st[1])] = {"desirable": "active", "auto": "passive"}.get(m, m)
        elif st[:2] == ["spanning-tree", "portfast"] or st[:2] == ["spanning-tree", "edgeport"]:
            if "disable" not in st:
                itf.stp_edge = True
        elif st[:2] == ["ip", "address"]:
            _add_ip(itf.ips, st[2:])
            itf.mode = "routed"
        elif st[:3] == ["ip", "dhcp", "snooping"] and "trust" in st:
            cfg.dhcp_snooping_trusted.add(itf.name)
        else:
            itf.unparsed.append(s)

    if itf.mode == "routed":
        return
    if mode == "trunk":
        itf.mode = "trunk"
        itf.allowed = allowed if allowed is not None and allowed != set(range(1, 4095)) else None
        if itf.allowed is None:
            itf.allowed = set(range(1, 4095))
    elif mode == "access" or (mode is None and itf.access_vlan):
        itf.mode = "access"
    elif mode == "hybrid":
        itf.mode = "hybrid"
        itf.unparsed.append("(hybrid 端口的 VLAN 配置需手动核对)")


def _parse_route(t):
    if t[0] == "vrf":
        return None
    if len(t) < 3 or not is_ip(t[0]):
        return None
    rt = Route(dest=t[0], mask=to_mask(t[1]))
    i = 2
    if i < len(t) and not is_ip(t[i]):
        rt.interface = t[i]
        i += 1
        if i < len(t) and re.match(r"^\d+(/\d+)*$", t[i]):  # 锐捷 'GigabitEthernet 0/1'
            rt.interface += " " + t[i]
            i += 1
    if i < len(t) and is_ip(t[i]):
        rt.nexthop = t[i]
        i += 1
    if i < len(t) and t[i].isdigit():
        rt.preference = int(t[i])
        i += 1
    if "name" in t[i:] or "description" in t[i:]:
        key = "name" if "name" in t[i:] else "description"
        rt.description = " ".join(t[t.index(key) + 1:])
    if not rt.nexthop and not rt.interface:
        return None
    return rt


def _parse_ospf(toks, subs):
    o = Ospf(pid=int(toks[2]) if len(toks) > 2 and toks[2].isdigit() else 1)
    for _, s in subs:
        st = s.split()
        k = st[0]
        if k == "router-id" and len(st) > 1:
            o.router_id = st[1]
        elif k == "network" and len(st) >= 5 and st[3] == "area":
            o.networks.append((st[1], to_wildcard(st[2]), area_str(st[4], "dotted")))
        elif k == "redistribute" and len(st) > 1:
            if st[1] == "connected":
                o.import_direct = True
            elif st[1] == "static":
                o.import_static = True
            else:
                o.unparsed.append(s)
        elif s.startswith("default-information originate"):
            o.default_originate = True
        elif k == "passive-interface" and len(st) > 1:
            o.silent.append(" ".join(st[1:]))
        else:
            o.unparsed.append(s)
    return o
