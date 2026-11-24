"""端到端测试：模拟 H3C 旧交换机 -> 锐捷新交换机 的完整串口流程。

运行: python -m tests.test_e2e   （需要 macOS/Linux）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swmigrate.console import SerialConsole, SwitchSession  # noqa: E402
from swmigrate.convert import auto_map, build_target  # noqa: E402
from swmigrate.generators import generate  # noqa: E402
from swmigrate.ifname import extract_physical_ports  # noqa: E402
from swmigrate.parsers import parse_config  # noqa: E402
from tests.fake_switch import FakeSwitch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    sample = (ROOT / "samples" / "h3c_s5560_sample.cfg").read_text(encoding="utf-8")

    # ---------- 旧交换机 (H3C, 需要登录, 有分页)
    old = FakeSwitch("h3c", sample, need_login=True)
    con = SerialConsole("fake", 9600, "utf-8", ser=old.ser)
    s = SwitchSession(con, log=print)
    s.login("admin", "secret")
    s.prepare()
    assert s.vendor == "h3c", s.vendor
    old.paging = True  # 强制测试分页处理
    cfg_text = s.get_config()
    con.close()
    assert "More" not in cfg_text, "分页符未清除"
    assert "sysname JX-Building3-Agg" in cfg_text
    assert "interface Ten-GigabitEthernet1/0/50" in cfg_text
    assert cfg_text.strip().endswith("return"), cfg_text[-200:]
    # 与原始配置逐行比对（去掉首尾空白）
    want = [ln.rstrip() for ln in sample.strip().splitlines()]
    got = [ln.rstrip() for ln in cfg_text.strip().splitlines()]
    if want != got:
        import difflib
        print("\n".join(list(difflib.unified_diff(want, got, lineterm=""))[:40]))
        raise AssertionError("读取到的配置与原始配置不一致")
    print(f"[PASS] 读取旧交换机配置 {len(got)} 行，与原始一致")

    src = parse_config(cfg_text, "h3c")

    # ---------- 新交换机 (锐捷, 无登录)
    new = FakeSwitch("ruijie", need_login=False)
    con = SerialConsole("fake", 9600, "utf-8", ser=new.ser)
    s = SwitchSession(con, log=print)
    s.login()
    s.prepare()
    assert s.vendor == "ruijie", s.vendor
    ports = extract_physical_ports(s.get_interfaces())
    assert len(ports) == 28, ports
    print(f"[PASS] 读取新交换机端口 {len(ports)} 个: {ports[0]} … {ports[-1]}")

    mapping = auto_map(src, "ruijie", ports)
    assert mapping["Ten-GigabitEthernet1/0/49"] == "TenGigabitEthernet 0/25", mapping
    assert mapping["Ten-GigabitEthernet1/0/52"] == "TenGigabitEthernet 0/28"
    assert mapping["GigabitEthernet1/0/24"] == "GigabitEthernet 0/24"
    print("[PASS] 自动端口映射: Ten-GigabitEthernet1/0/49 -> TenGigabitEthernet 0/25")

    tgt, warns = build_target(src, mapping, "ruijie")
    lines = generate(tgt, "ruijie")
    t0 = time.time()
    res = s.push(lines)
    s.save()
    con.close()
    fails = [r for r in res if not r[1]]
    sent = [x for x in new.received if x]
    assert len(res) == len(lines), (len(res), len(lines))
    assert all(ln.strip() in sent for ln in lines)
    assert len(fails) == 1 and "redistribute connected" in fails[0][0], fails
    assert sent[-1] == "write"
    print(f"[PASS] 下发 {len(res)} 行用时 {time.time() - t0:.1f}s，正确识别出 1 条失败命令: {fails[0][0]}")
    print("全部通过")


if __name__ == "__main__":
    main()
