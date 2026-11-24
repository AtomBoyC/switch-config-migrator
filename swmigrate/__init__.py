"""交换机配置迁移工具 (Switch Config Migrator)."""
__version__ = "1.0.0"

VENDORS = ["h3c", "huawei", "ruijie", "cisco"]
VENDOR_NAMES = {
    "h3c": "华三 H3C (Comware)",
    "huawei": "华为 Huawei (VRP)",
    "ruijie": "锐捷 Ruijie (RGOS)",
    "cisco": "思科 Cisco (IOS)",
}
