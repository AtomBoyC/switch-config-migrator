"""程序入口：双击运行 / python main.py 启动图形界面。

命令行离线转换: python main.py --cli 旧配置.cfg --to ruijie
"""
import sys


def _selftest():
    """打包后自检：构建界面、解析示例配置、生成锐捷配置后退出。"""
    from swmigrate.gui import App
    sample = ("#\n sysname T\n#\nvlan 10\n#\ninterface Vlan-interface10\n ip address 10.0.0.1 24\n#\n"
              "interface GigabitEthernet1/0/1\n port access vlan 10\n#\nreturn\n")
    a = App()
    a.withdraw()
    a._set_old_text(sample)
    a.generate_cfg()
    out = a.cfg_txt.get("1.0", "end-1c")
    import serial  # noqa: F401  确认 pyserial 已打包
    a.destroy()
    ok = "interface VLAN 10" in out and "switchport access vlan 10" in out
    print("SELFTEST", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from swmigrate.cli import main as cli_main
        sys.exit(cli_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    from swmigrate.gui import main
    main()
