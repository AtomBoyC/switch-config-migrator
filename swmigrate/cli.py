"""命令行版本：离线转换配置文件（不连接设备）。

用法:
  python -m swmigrate.cli 旧配置.cfg --from h3c --to ruijie [--ports 新设备端口列表.txt] [-o 输出.txt]
"""
import argparse
import sys

from . import VENDORS
from .convert import auto_map, build_target
from .generators import generate
from .ifname import extract_physical_ports
from .parsers import detect_vendor_from_config, parse_config


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description="交换机配置跨厂商转换（离线）")
    ap.add_argument("config")
    ap.add_argument("--from", dest="src", choices=VENDORS)
    ap.add_argument("--to", dest="dst", choices=VENDORS, required=True)
    ap.add_argument("--ports", help="新交换机 'display interface brief/show interface status' 输出文件")
    ap.add_argument("--template", help="无端口列表时的新端口编号模板, 如 0/{n}")
    ap.add_argument("-o", "--output")
    a = ap.parse_args(argv)

    text = read_text(a.config)
    src = a.src or detect_vendor_from_config(text)
    if not src:
        print("无法识别源配置厂商，请用 --from 指定", file=sys.stderr)
        return 2
    cfg = parse_config(text, src)
    ports = extract_physical_ports(read_text(a.ports)) if a.ports else None
    mapping = auto_map(cfg, a.dst, ports, a.template)
    new, warns = build_target(cfg, mapping, a.dst)
    lines = generate(new, a.dst)

    out = "\n".join(lines) + "\n"
    if a.output:
        open(a.output, "w", encoding="utf-8").write(out)
    else:
        sys.stdout.write(out)

    print(f"\n# 源厂商: {src}  目标厂商: {a.dst}", file=sys.stderr)
    print("# 端口映射:", file=sys.stderr)
    for k, v in mapping.items():
        print(f"#   {k:30s} -> {v or '(未映射)'}", file=sys.stderr)
    print("# 提示/警告:", file=sys.stderr)
    for w in warns:
        print("#   - " + w, file=sys.stderr)
    if new.unparsed_blocks:
        print(f"# 有 {len(new.unparsed_blocks)} 段配置未自动转换(需人工处理):", file=sys.stderr)
        for b in new.unparsed_blocks:
            print("#   " + "\n#   ".join(b), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
