"""配置解析器：把各厂商的配置文本解析为 SwitchConfig。"""
from .comware import parse_comware
from .ios import parse_ios


def parse_config(text, vendor):
    if vendor in ("h3c", "huawei"):
        return parse_comware(text, vendor)
    if vendor in ("ruijie", "cisco"):
        return parse_ios(text, vendor)
    raise ValueError(f"不支持的厂商: {vendor}")


def detect_vendor_from_config(text):
    t = text[:20000]
    tl = t.lower()
    if "vlan-interface" in tl or "bridge-aggregation" in tl or "port link-aggregation group" in tl \
            or "ten-gigabitethernet" in tl or "h3c" in tl or "comware" in tl:
        return "h3c"
    if "vlanif" in tl or "eth-trunk" in tl or "xgigabitethernet" in tl or "huawei" in tl \
            or "vlan batch" in tl:
        return "huawei"
    if "aggregateport" in tl or "ruijie" in tl or "interface vlan " in tl \
            or "rgos" in tl or "port-group" in tl:
        return "ruijie"
    if "port-channel" in tl or "switchport" in tl or "cisco" in tl:
        # 锐捷也用 switchport, 用接口名空格特征区分
        import re
        if re.search(r"^interface \S+ \d", t, re.M):
            return "ruijie"
        return "cisco"
    if "sysname" in tl:
        return "h3c"
    return ""
