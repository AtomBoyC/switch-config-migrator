"""端口映射与配置转换（旧设备模型 -> 新设备模型）。"""
import copy

from .ifname import PHYSICAL_KINDS, canonical, format_ifname, parse_ifname_any, sort_key
from .model import SwitchConfig

DEFAULT_TEMPLATES = {
    "ruijie": "0/{n}",
    "h3c": "1/0/{n}",
    "huawei": "0/0/{n}",
    "cisco": "1/0/{n}",
}


def auto_map(cfg, target_vendor, target_ports=None, template=None):
    """返回 {旧端口名: 新端口名}。

    target_ports: 从新交换机读取到的物理端口列表；为空时按 template 生成端口名。
    策略：同速率类型的端口按顺序一一对应（旧的第 N 个千兆口 -> 新的第 N 个千兆口），
    这样新设备上线时跳线可以按原位置插。
    """
    mapping = {}
    old_by_kind = {}
    for itf in cfg.physical_sorted():
        old_by_kind.setdefault(itf.kind, []).append(itf)

    if target_ports:
        # 按 (类型, 槽位前缀) 分组，例如 ('GE', (1,0)) ；堆叠/多槽位按顺序配对
        new_groups = {}
        for p in sorted(target_ports, key=sort_key):
            r = parse_ifname_any(p)
            if r and r[0] in PHYSICAL_KINDS:
                new_groups.setdefault(r[0], {}).setdefault(r[1][:-1], []).append(
                    (r[1][-1], canonical(p, target_vendor)))
        for kind, olds in old_by_kind.items():
            old_slots = {}
            for itf in olds:
                old_slots.setdefault(itf.nums[:-1], []).append(itf)
            new_slot_keys = sorted(new_groups.get(kind, {}))
            for si, sk in enumerate(sorted(old_slots)):
                group = old_slots[sk]
                news = new_groups[kind][new_slot_keys[si]] if si < len(new_slot_keys) else []
                base_old = min(i.nums[-1] for i in group)
                if kind in ("GE", "FE") and base_old > 1:
                    base_old = 1   # 千兆/百兆口通常从 1 开始编号（配置里可能未列出前面的端口）
                for itf in group:
                    idx = itf.nums[-1] - base_old
                    mapping[itf.name] = news[idx][1] if 0 <= idx < len(news) else ""
    else:
        tpl = template or DEFAULT_TEMPLATES.get(target_vendor, "0/{n}")
        for kind, olds in old_by_kind.items():
            for itf in olds:
                n = itf.nums[-1] if itf.nums else 0
                try:
                    nums = tuple(int(x) for x in tpl.format(n=n).split("/"))
                    mapping[itf.name] = format_ifname(target_vendor, kind, nums)
                except Exception:
                    mapping[itf.name] = ""
    return mapping


def build_target(cfg: SwitchConfig, mapping, target_vendor, all_vlans=None):
    """按端口映射生成目标设备模型。返回 (new_cfg, warnings)。"""
    new = copy.deepcopy(cfg)
    new.vendor = target_vendor
    warns = list(cfg.warnings)
    full = set(range(1, 4095))

    new.interfaces = {}
    used = {}
    for itf in cfg.physical_sorted():
        target = (mapping.get(itf.name) or "").strip()
        if not target:
            if itf.has_config and not (itf.shutdown and not itf.mode and not itf.description):
                warns.append(f"端口 {itf.name} 有配置但未映射到新设备端口，已跳过")
            continue
        target = canonical(target, target_vendor)
        if target in used:
            warns.append(f"新端口 {target} 被重复映射（{used[target]} 和 {itf.name}），后者已跳过")
            continue
        used[target] = itf.name
        n = copy.deepcopy(itf)
        r = parse_ifname_any(target)
        n.name = target
        if r:
            n.kind, n.nums = r
        _normalize_port(n, warns, itf.name)
        new.interfaces[target] = n

    # 聚合口
    new.lags = {}
    for lid, lag in cfg.lags.items():
        n = copy.deepcopy(lag)
        n.name = format_ifname(target_vendor, "LAG", (lid,))
        _normalize_port(n, warns, lag.name)
        if not any(i.lag_id == lid for i in new.interfaces.values()):
            warns.append(f"聚合口 {lag.name} 没有任何成员口被映射，请检查")
        new.lags[lid] = n

    # 静态路由出接口名转换
    for rt in new.routes:
        if rt.interface and rt.interface.upper() != "NULL0":
            rt.interface = canonical(rt.interface, target_vendor)
    for o in new.ospf:
        o.silent = [canonical(s, target_vendor) for s in o.silent]

    # DHCP snooping 信任口改名
    trusted = set()
    for old in cfg.dhcp_snooping_trusted:
        if old in mapping and mapping[old]:
            trusted.add(canonical(mapping[old], target_vendor))
        else:
            r = parse_ifname_any(old)
            if r and r[0] == "LAG":
                trusted.add(format_ifname(target_vendor, "LAG", r[1]))
    new.dhcp_snooping_trusted = trusted

    # 端口上无法识别的命令提示
    for itf in list(new.interfaces.values()) + list(new.lags.values()):
        if itf.unparsed:
            warns.append(f"{itf.name} 有 {len(itf.unparsed)} 条命令未转换: " + "; ".join(itf.unparsed[:6]))
    for svi in new.svis.values():
        if svi.unparsed:
            warns.append(f"VLAN 接口 {svi.vlan} 有命令未转换: " + "; ".join(svi.unparsed[:6]))
    for o in new.ospf:
        if o.unparsed:
            warns.append(f"OSPF {o.pid} 有命令未转换: " + "; ".join(o.unparsed[:6]))

    if target_vendor in ("ruijie", "cisco"):
        for itf in list(new.interfaces.values()) + list(new.lags.values()):
            if itf.mode == "trunk" and itf.allowed is not None and itf.allowed >= full:
                itf.allowed = None
    return new, warns


def _normalize_port(itf, warns, oldname):
    """把 hybrid 端口尽量转换为 access/trunk。"""
    if itf.mode != "hybrid":
        return
    un = set(itf.hybrid_untagged)
    tg = set(itf.hybrid_tagged)
    pvid = itf.native_vlan or 1
    if not tg and (un <= {pvid} or len(un) == 1):
        itf.mode = "access"
        itf.access_vlan = pvid if pvid in un or not un else next(iter(un))
        itf.native_vlan = None
        warns.append(f"{oldname}: hybrid 端口已转换为 access vlan {itf.access_vlan}")
    elif un <= {pvid}:
        itf.mode = "trunk"
        itf.native_vlan = pvid
        itf.allowed = tg | un | {pvid}
        warns.append(f"{oldname}: hybrid 端口已转换为 trunk (native {pvid})")
    else:
        itf.mode = "trunk"
        itf.native_vlan = pvid
        itf.allowed = tg | un | {pvid}
        warns.append(f"{oldname}: hybrid 端口有多个 untagged VLAN {sorted(un)}，"
                     f"已近似转换为 trunk (native {pvid})，请务必人工核对！")
    itf.hybrid_tagged = set()
    itf.hybrid_untagged = set()
