"""与厂商无关的中间配置模型。"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Interface:
    name: str                      # 原始名称
    kind: str = ""                 # GE/10GE/LAG/...
    nums: Tuple[int, ...] = ()
    description: str = ""
    shutdown: bool = False
    mode: Optional[str] = None     # access / trunk / hybrid / routed / None(默认)
    access_vlan: Optional[int] = None
    native_vlan: Optional[int] = None
    allowed: Optional[Set[int]] = None        # trunk 允许 VLAN, None = 设备默认
    hybrid_untagged: Set[int] = field(default_factory=set)
    hybrid_tagged: Set[int] = field(default_factory=set)
    lag_id: Optional[int] = None              # 成员口所属聚合组
    lag_mode: Optional[str] = None            # 聚合口: active/passive/on
    ips: List[Tuple[str, str, bool]] = field(default_factory=list)  # (ip, mask, secondary)
    stp_edge: bool = False
    unparsed: List[str] = field(default_factory=list)

    @property
    def has_config(self):
        return bool(self.description or self.shutdown or self.mode or self.access_vlan
                    or self.allowed is not None or self.lag_id or self.ips or self.stp_edge
                    or self.native_vlan or self.unparsed)


@dataclass
class Vrrp:
    vrid: int
    vips: List[str] = field(default_factory=list)
    priority: Optional[int] = None
    preempt_delay: Optional[int] = None


@dataclass
class Svi:
    vlan: int
    description: str = ""
    shutdown: bool = False
    ips: List[Tuple[str, str, bool]] = field(default_factory=list)
    dhcp_relay: List[str] = field(default_factory=list)
    vrrp: Dict[int, Vrrp] = field(default_factory=dict)
    unparsed: List[str] = field(default_factory=list)


@dataclass
class Loopback:
    num: int
    ips: List[Tuple[str, str, bool]] = field(default_factory=list)
    description: str = ""


@dataclass
class Route:
    dest: str
    mask: str
    nexthop: str = ""
    interface: str = ""
    preference: Optional[int] = None
    description: str = ""


@dataclass
class Ospf:
    pid: int
    router_id: str = ""
    networks: List[Tuple[str, str, str]] = field(default_factory=list)  # (net, wildcard, area)
    import_direct: bool = False
    import_static: bool = False
    default_originate: bool = False
    silent: List[str] = field(default_factory=list)
    unparsed: List[str] = field(default_factory=list)


@dataclass
class SwitchConfig:
    vendor: str = ""
    hostname: str = ""
    vlans: Dict[int, str] = field(default_factory=dict)   # id -> name
    interfaces: Dict[str, Interface] = field(default_factory=dict)  # 物理口, key=原始名
    lags: Dict[int, Interface] = field(default_factory=dict)
    svis: Dict[int, Svi] = field(default_factory=dict)
    loopbacks: Dict[int, Loopback] = field(default_factory=dict)
    routes: List[Route] = field(default_factory=list)
    ospf: List[Ospf] = field(default_factory=list)
    stp_mode: Optional[str] = None
    stp_priority: Optional[int] = None
    stp_enabled: Optional[bool] = None
    snmp_communities: List[Tuple[str, str]] = field(default_factory=list)  # (name, ro/rw)
    snmp_location: str = ""
    snmp_contact: str = ""
    ntp_servers: List[str] = field(default_factory=list)
    dhcp_snooping: bool = False
    dhcp_snooping_trusted: Set[str] = field(default_factory=set)
    warnings: List[str] = field(default_factory=list)
    unparsed_blocks: List[List[str]] = field(default_factory=list)  # 未转换的原始配置段

    def physical_sorted(self):
        from .ifname import sort_key
        return sorted(self.interfaces.values(), key=lambda i: sort_key(i.name))
