"""接口名称识别与各厂商命名转换。

把各家写法统一成 (kind, numbers)，例如:
  H3C   Ten-GigabitEthernet1/0/49  -> ("10GE", (1,0,49))
  华为  XGigabitEthernet0/0/1      -> ("10GE", (0,0,1))
  锐捷  TenGigabitEthernet 0/49    -> ("10GE", (0,49))
"""
import re

PHYSICAL_KINDS = ["FE", "GE", "10GE", "25GE", "40GE", "100GE"]

# 前缀 (小写, 去空格) -> 类型。匹配时按长度从长到短。
_PREFIX = {
    # FE
    "fastethernet": "FE", "ethernet": "FE", "fa": "FE", "fe": "FE", "eth": "FE",
    # GE
    "gigabitethernet": "GE", "gigaethernet": "GE", "gi": "GE", "ge": "GE", "gige": "GE",
    # 10GE
    "ten-gigabitethernet": "10GE", "tengigabitethernet": "10GE", "xgigabitethernet": "10GE",
    "tengige": "10GE", "te": "10GE", "xge": "10GE", "tge": "10GE", "10ge": "10GE",
    # 25GE
    "twenty-fivegige": "25GE", "twentyfivegige": "25GE", "twentyfivegigabitethernet": "25GE",
    "tfgigabitethernet": "25GE", "25gigabitethernet": "25GE", "25ge": "25GE", "wge": "25GE",
    "tf": "25GE", "twe": "25GE",
    # 40GE
    "fortygige": "40GE", "fortygigabitethernet": "40GE", "40ge": "40GE", "fge": "40GE",
    "fo": "40GE",
    # 100GE
    "hundredgige": "100GE", "hundredgigabitethernet": "100GE", "100ge": "100GE",
    "hge": "100GE", "hu": "100GE",
    # 管理口
    "m-gigabitethernet": "MGMT", "meth": "MGMT", "mgmt": "MGMT", "mge": "MGMT",
    "mgmtethernet": "MGMT", "management": "MGMT",
    # 聚合
    "bridge-aggregation": "LAG", "eth-trunk": "LAG", "aggregateport": "LAG",
    "port-channel": "LAG", "bagg": "LAG", "po": "LAG", "ag": "LAG",
    "route-aggregation": "RLAG", "ragg": "RLAG",
    # 三层
    "vlan-interface": "VLAN", "vlanif": "VLAN", "vlan": "VLAN", "vl": "VLAN",
    "loopback": "LOOP", "loop": "LOOP", "lo": "LOOP",
    "null": "NULL", "inloop": "OTHER", "reg": "OTHER", "tunnel": "OTHER",
}
_PREFIX_SORTED = sorted(_PREFIX.items(), key=lambda kv: -len(kv[0]))

_IF_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\-]*?)\s?(\d+(?:/\d+)*)(?:\.(\d+))?\s*$")


def parse_ifname(name):
    """返回 (kind, nums tuple) 或 None。"""
    if not name:
        return None
    m = _IF_RE.match(name.strip())
    if not m:
        return None
    prefix = m.group(1).lower()
    # 处理 '25GE1/0/1' 这种前缀以数字开头的情况
    nums = tuple(int(x) for x in m.group(2).split("/"))
    for p, k in _PREFIX_SORTED:
        if prefix == p:
            return (k, nums)
    return None


_NUMPREFIX_RE = re.compile(r"^\s*(25GE|40GE|100GE|10GE)\s?(\d+(?:/\d+)*)\s*$", re.I)


def parse_ifname_any(name):
    r = parse_ifname(name)
    if r:
        return r
    m = _NUMPREFIX_RE.match(name or "")
    if m:
        return (m.group(1).upper(), tuple(int(x) for x in m.group(2).split("/")))
    return None


VENDOR_NAMES = {
    "h3c": {"FE": "Ethernet", "GE": "GigabitEthernet", "10GE": "Ten-GigabitEthernet",
            "25GE": "Twenty-FiveGigE", "40GE": "FortyGigE", "100GE": "HundredGigE",
            "MGMT": "M-GigabitEthernet", "LAG": "Bridge-Aggregation", "VLAN": "Vlan-interface",
            "LOOP": "LoopBack", "RLAG": "Route-Aggregation"},
    "huawei": {"FE": "Ethernet", "GE": "GigabitEthernet", "10GE": "XGigabitEthernet",
               "25GE": "25GE", "40GE": "40GE", "100GE": "100GE", "MGMT": "MEth",
               "LAG": "Eth-Trunk", "VLAN": "Vlanif", "LOOP": "LoopBack", "RLAG": "Eth-Trunk"},
    "ruijie": {"FE": "FastEthernet", "GE": "GigabitEthernet", "10GE": "TenGigabitEthernet",
               "25GE": "TFGigabitEthernet", "40GE": "FortyGigabitEthernet",
               "100GE": "HundredGigabitEthernet", "MGMT": "Mgmt", "LAG": "AggregatePort",
               "VLAN": "VLAN", "LOOP": "Loopback", "RLAG": "AggregatePort"},
    "cisco": {"FE": "FastEthernet", "GE": "GigabitEthernet", "10GE": "TenGigabitEthernet",
              "25GE": "TwentyFiveGigE", "40GE": "FortyGigabitEthernet", "100GE": "HundredGigE",
              "MGMT": "GigabitEthernet", "LAG": "Port-channel", "VLAN": "Vlan",
              "LOOP": "Loopback", "RLAG": "Port-channel"},
}

_SEP = {"h3c": "", "huawei": "", "ruijie": " ", "cisco": ""}


def format_ifname(vendor, kind, nums):
    base = VENDOR_NAMES[vendor].get(kind)
    if base is None:
        raise ValueError(f"{vendor} 不支持接口类型 {kind}")
    return f"{base}{_SEP[vendor]}{'/'.join(str(n) for n in nums)}"


def canonical(name, vendor):
    """把任意写法转换为目标厂商的规范写法；无法识别时原样返回。"""
    r = parse_ifname_any(name)
    if not r:
        return name
    try:
        return format_ifname(vendor, r[0], r[1])
    except ValueError:
        return name


def is_physical(name):
    r = parse_ifname_any(name)
    return bool(r and r[0] in PHYSICAL_KINDS)


def sort_key(name):
    r = parse_ifname_any(name)
    if not r:
        return (99, (), name)
    order = PHYSICAL_KINDS + ["MGMT", "LAG", "RLAG", "VLAN", "LOOP", "NULL", "OTHER"]
    return (order.index(r[0]) if r[0] in order else 98, r[1], name)


_LINE_IF_RE = re.compile(r"^\s*\*?\s*([A-Za-z][A-Za-z0-9\-]*?\s?\d+(?:/\d+)+)\b")
_LINE_IF_RE2 = re.compile(r"^\s*\*?\s*((?:25GE|40GE|100GE|10GE)\d+(?:/\d+)+)\b", re.I)


def extract_physical_ports(text):
    """从 'display interface brief' / 'show interface status' 输出中提取物理端口名称。"""
    seen = []
    for line in text.splitlines():
        m = _LINE_IF_RE2.match(line) or _LINE_IF_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if is_physical(name) and name not in seen:
            seen.append(name)
    return seen
