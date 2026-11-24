"""通用工具：VLAN 列表、掩码转换等。"""
import ipaddress
import re


def parse_vlan_tokens(tokens):
    """解析 'H3C/华为' 风格的 VLAN 列表: ['10', '20', 'to', '30'] -> {10, 20..30}.

    也兼容 '10-20'、'10,20'。'all' 返回 1..4094。
    """
    result = set()
    toks = []
    for t in tokens:
        toks.extend([x for x in re.split(r",", t) if x])
    i = 0
    while i < len(toks):
        t = toks[i].lower()
        if t == "all":
            return set(range(1, 4095))
        if "-" in t:
            a, b = t.split("-", 1)
            if a.isdigit() and b.isdigit():
                result.update(range(int(a), int(b) + 1))
            i += 1
            continue
        if t.isdigit():
            if i + 2 < len(toks) and toks[i + 1].lower() == "to" and toks[i + 2].isdigit():
                result.update(range(int(t), int(toks[i + 2]) + 1))
                i += 3
                continue
            result.add(int(t))
        i += 1
    return result


def parse_vlan_list_str(s):
    """解析 '1,3-5,10' 风格字符串."""
    return parse_vlan_tokens(re.split(r"[\s,]+", s.strip()))


def vlan_ranges(vlans):
    """{1,2,3,5} -> [(1,3),(5,5)]"""
    vs = sorted(set(vlans))
    out = []
    for v in vs:
        if out and v == out[-1][1] + 1:
            out[-1] = (out[-1][0], v)
        else:
            out.append((v, v))
    return out


def vlan_str_dash(vlans):
    """{1,2,3,5} -> '1-3,5' (锐捷/思科风格)"""
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in vlan_ranges(vlans))


def vlan_str_to(vlans, max_items=10):
    """{1,2,3,5} -> ['1 to 3 5'] (华三/华为风格, 每条命令最多 max_items 段)"""
    parts = [f"{a}" if a == b else f"{a} to {b}" for a, b in vlan_ranges(vlans)]
    return [" ".join(parts[i:i + max_items]) for i in range(0, len(parts), max_items)]


def to_mask(m):
    """'24' 或 '255.255.255.0' -> '255.255.255.0'"""
    m = str(m)
    if m.isdigit():
        return str(ipaddress.IPv4Network(f"0.0.0.0/{m}").netmask)
    return m


def to_wildcard(mask):
    """255.255.255.0 -> 0.0.0.255; 如果本身就是反掩码则原样返回."""
    m = to_mask(mask)
    parts = [int(x) for x in m.split(".")]
    # 判断是否已经是反掩码（首字节为 0 且非全 0 掩码）
    if parts[0] == 0 and any(parts):
        return m
    return ".".join(str(255 - p) for p in parts)


def is_ip(s):
    try:
        ipaddress.IPv4Address(s)
        return True
    except Exception:
        return False


def area_str(a, style="dotted"):
    """OSPF 区域号转换: '0.0.0.0' <-> '0'"""
    if style == "dotted":
        if a.isdigit():
            return str(ipaddress.IPv4Address(int(a)))
        return a
    if is_ip(a):
        return str(int(ipaddress.IPv4Address(a)))
    return a
