"""模拟交换机（用于在没有真机的情况下测试串口流程）。

提供一个与 pyserial.Serial 接口兼容的 .ser 对象，模拟 H3C 旧交换机 / 锐捷新交换机。
"""
import os
import queue
import re
import threading
import time


class _FakeSerial:
    """pyserial 兼容对象：write() 交给模拟设备处理，read() 读取设备输出。"""

    def __init__(self, dev):
        self.dev = dev
        self.outq = queue.Queue()

    def write(self, b):
        self.dev.feed(bytes(b))
        return len(b)

    def flush(self):
        pass

    def read(self, n):
        try:
            data = self.outq.get(timeout=0.1)
        except queue.Empty:
            return b""
        chunks = [data]
        try:
            while True:
                chunks.append(self.outq.get_nowait())
        except queue.Empty:
            pass
        return b"".join(chunks)

    def close(self):
        pass


class FakeSwitch:
    def __init__(self, kind="h3c", config_text="", need_login=True, page_lines=20):
        self.kind = kind
        self.config_text = config_text
        self.need_login = need_login
        self.page_lines = page_lines
        self.ser = _FakeSerial(self)
        self.state = "login_user" if need_login else "cli"
        self.paging = True
        self.mode = "user"
        self.received = []
        self._pending_pages = []
        self._buf = b""
        self._lock = threading.Lock()

    # --- 输出
    def out(self, s):
        self.ser.outq.put(s.replace("\n", "\r\n").encode("utf-8"))

    def prompt(self):
        if self.kind == "h3c":
            return {"user": "<OLD-H3C>", "sys": "[OLD-H3C]"}.get(self.mode, f"[OLD-H3C-{self.mode}]")
        return {"user": "Ruijie>", "en": "Ruijie#", "conf": "Ruijie(config)#"}.get(
            self.mode, f"Ruijie(config-{self.mode})#")

    def feed(self, d):
        with self._lock:
            buf = self._buf + d
            while True:
                if self._pending_pages and b" " in buf:
                    buf = buf.replace(b" ", b"", 1)
                    self._send_page()
                    continue
                if b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    self._handle(line.decode("utf-8", "replace").strip("\n"))
                    continue
                if b"\x04" in buf:
                    buf = buf.replace(b"\x04", b"")
                    continue
                break
            self._buf = buf

    def _send_page(self):
        page = self._pending_pages.pop(0)
        # 模拟华三分页符的清除序列
        self.out("\x1b[16D                \x1b[16D" + page)
        if self._pending_pages:
            self.out("\n---- More ----")
        else:
            self.out("\n" + self.prompt())

    def _paged(self, text):
        lines = text.split("\n")
        if not self.paging:
            self.out(text + "\n" + self.prompt())
            return
        pages = ["\n".join(lines[i:i + self.page_lines]) for i in range(0, len(lines), self.page_lines)]
        self._pending_pages = pages
        first = self._pending_pages.pop(0)
        self.out(first)
        if self._pending_pages:
            self.out("\n---- More ----")
        else:
            self.out("\n" + self.prompt())

    def _handle(self, line):
        if self.state == "login_user":
            if line == "":
                self.out("\nLogin authentication\n\nUsername:")
                self.state = "login_user2"
            return
        if self.state == "login_user2":
            self.out(line + "\nPassword:")
            self.state = "login_pass"
            self._user = line
            return
        if self.state == "login_pass":
            if line == "secret":
                self.out("\n\n" + self.prompt())
                self.state = "cli"
            else:
                self.out("\nPassword error\n\nUsername:")
                self.state = "login_user2"
            return
        self.received.append(line)
        self.out(line + "\n")
        cmd = line.strip()
        if self.kind == "h3c":
            self._h3c(cmd)
        else:
            self._ruijie(cmd)

    def _h3c(self, cmd):
        if cmd == "":
            pass
        elif cmd.startswith("dis") and "version" in cmd:
            self.out("H3C Comware Software, Version 7.1.070, Release 6328P03\n"
                     "Copyright (c) 2004-2020 New H3C Technologies Co., Ltd.\n")
        elif cmd == "screen-length disable":
            self.paging = False
        elif cmd.startswith("dis") and "current" in cmd:
            self._paged(self.config_text)
            return
        elif cmd == "system-view":
            self.mode = "sys"
        elif cmd == "return":
            self.mode = "user"
        self.out(self.prompt())

    def _ruijie(self, cmd):
        if cmd == "":
            pass
        elif cmd == "enable":
            self.mode = "en"
        elif cmd == "show version":
            self.out("System description      : Ruijie Full Gigabit Layer 3 Switch(S5750C-28GT4XS-H)\n"
                     "System software version : S5750_RGOS 11.4(1)B74P2\n")
        elif cmd == "terminal length 0":
            self.paging = False
        elif cmd == "show interface status":
            s = "Interface                        Status    Vlan   Duplex   Speed     Type\n"
            s += "-------------------------------- --------  ----   -------  --------- ------\n"
            for i in range(1, 25):
                s += f"GigabitEthernet 0/{i:<17d}down      1      Unknown  Unknown   copper\n"
            for i in range(25, 29):
                s += f"TenGigabitEthernet 0/{i:<14d}down      1      Unknown  Unknown   fiber\n"
            self.out(s)
        elif cmd == "configure terminal":
            self.out("Enter configuration commands, one per line.  End with CNTL/Z.\n")
            self.mode = "conf"
        elif cmd == "end":
            self.mode = "en"
        elif cmd == "write":
            self.out("Building configuration...\n[OK]\n")
        elif self.mode != "user" and self.mode != "en":
            m = re.match(r"interface (.+)", cmd)
            if cmd == "exit":
                self.mode = "conf"
            elif m:
                self.mode = "if-" + m.group(1)
            elif cmd.startswith("vlan"):
                self.mode = "vlan"
            elif cmd.startswith("router ospf"):
                self.mode = "router"
            elif "redistribute connected" in cmd:
                self.out("                                  ^\n% Invalid input detected at '^' marker.\n")
        self.out(self.prompt())
