"""通过 Console 串口与交换机交互：登录、识别厂商、读取配置、逐行下发。"""
import re
import threading
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # 允许在没有 pyserial 的环境下导入（离线模式）
    serial = None

# 提示符
PROMPT_COMWARE = re.compile(r"(?:^|[\r\n])\s*([<\[][^\r\n<>\[\]]{1,80}[>\]])\s*$")
PROMPT_IOS = re.compile(r"(?:^|[\r\n])\s*([A-Za-z0-9_.\-@/:]{1,63}(?:\([^\r\n)]{0,60}\))?[>#])\s*$")
MORE_RE = re.compile(r"(-{2,}\s*More\s*-{2,}|--More--|<--- More --->|\(more\)|Press any key to continue)",
                     re.I)

ERROR_PATTERNS = re.compile(
    r"(% ?Unrecognized command|% ?Incomplete command|% ?Wrong parameter|% ?Too many parameters|"
    r"% ?Ambiguous command|% ?Invalid input|% ?Unknown command|% ?Invalid parameter|"
    r"^\s*Error:|Error: |% ?Error|% ?Failed|% ?Command fail|% ?Cannot|% ?Can't|"
    r"% ?Invalid|% ?Interface does not exist|% ?The specified)", re.I | re.M)

CONFIRM_RE = re.compile(r"(\[Y/N\]|\[y/n\]|\[yes/no\]|\(y/n\)|\[Y/N\]:|continue\?|Are you sure)", re.I)


class SessionError(Exception):
    pass


def list_serial_ports():
    if serial is None:
        return []
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append((p.device, f"{p.device}  {p.description or ''}".strip()))
    return ports


def _render_terminal(raw):
    """简易终端模拟：处理退格、ESC[nD 光标左移、回车覆盖，消除分页符残留。"""
    raw = raw.replace("\r\n", "\n")
    out_lines = []
    for seg in raw.split("\n"):
        buf = []
        cur = 0
        i = 0
        n = len(seg)
        while i < n:
            c = seg[i]
            if c == "\x1b":
                m = re.match(r"\x1b\[(\d*)([A-Za-z])", seg[i:])
                if m:
                    cnt = int(m.group(1) or 1)
                    cmd = m.group(2)
                    if cmd == "D":
                        cur = max(0, cur - cnt)
                    elif cmd == "C":
                        cur += cnt
                    elif cmd == "K":
                        del buf[cur:]
                    i += len(m.group(0))
                    continue
                i += 1
                continue
            if c == "\b":
                cur = max(0, cur - 1)
            elif c == "\r":
                cur = 0
            elif c == "\x07" or c == "\x00":
                pass
            else:
                while len(buf) < cur:
                    buf.append(" ")
                if cur < len(buf):
                    buf[cur] = c
                else:
                    buf.append(c)
                cur += 1
            i += 1
        line = "".join(buf).rstrip()
        line = MORE_RE.sub("", line).rstrip()
        out_lines.append(line)
    return "\n".join(out_lines)


class SerialConsole:
    """串口收发封装：后台线程持续读取，数据进入缓冲并回调给界面终端显示。"""

    def __init__(self, port, baudrate=9600, encoding="utf-8", on_data=None, ser=None):
        if ser is None and serial is None:
            raise SessionError("未安装 pyserial，请先执行: pip install pyserial")
        self.encoding = encoding
        self.on_data = on_data
        if ser is not None:
            self.ser = ser  # 测试用：传入模拟设备
        else:
            try:
                self.ser = serial.Serial(port=port, baudrate=baudrate, bytesize=8, parity="N",
                                         stopbits=1, timeout=0.1, xonxoff=False, rtscts=False)
            except serial.SerialException as e:
                raise SessionError(f"无法打开串口 {port}: {e}\n（串口可能被 SecureCRT 等软件占用，请先断开）")
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while self._alive:
            try:
                data = self.ser.read(4096)
            except Exception:
                break
            if data:
                with self._cv:
                    self._buf.extend(data)
                    self._cv.notify_all()
                if self.on_data:
                    try:
                        self.on_data(self.decode(data))
                    except Exception:
                        pass

    def decode(self, data):
        try:
            return bytes(data).decode(self.encoding)
        except UnicodeDecodeError:
            try:
                return bytes(data).decode("gbk")
            except UnicodeDecodeError:
                return bytes(data).decode(self.encoding, errors="replace")

    def write(self, text):
        self.ser.write(text.encode(self.encoding, errors="replace"))
        self.ser.flush()

    def write_raw(self, b):
        self.ser.write(b)
        self.ser.flush()

    def clear(self):
        with self._cv:
            self._buf.clear()

    def take(self):
        with self._cv:
            data = bytes(self._buf)
            self._buf.clear()
        return data

    def peek_text(self):
        with self._cv:
            return self.decode(self._buf)

    def wait_data(self, timeout):
        with self._cv:
            return self._cv.wait(timeout)

    def close(self):
        self._alive = False
        try:
            self.ser.close()
        except Exception:
            pass


class SwitchSession:
    """交换机会话：负责登录、识别厂商、关闭分页、执行命令、进入配置模式下发。"""

    def __init__(self, console, log=None):
        self.c = console
        self.vendor = None
        self.family = None  # 'comware' | 'ios'
        self.prompt = ""
        self.log = log or (lambda msg: None)
        self.stop_flag = False

    # ---------- 基础收发 ----------
    def _tail(self, text, n=300):
        return text[-n:]

    def _match_prompt(self, text):
        t = _render_terminal(text[-400:])
        t = t.rstrip("\n")
        last = t.split("\n")[-1] if t else ""
        m = PROMPT_COMWARE.search("\n" + last)
        if m:
            return "comware", m.group(1)
        m = PROMPT_IOS.search("\n" + last)
        if m:
            return "ios", m.group(1)
        return None, None

    def read_until_prompt(self, timeout=15, auto_more=True, auto_confirm=False, idle_timeout=None):
        """读取直到出现提示符。返回原始文本。"""
        acc = ""
        end = time.time() + timeout
        last_data = time.time()
        while time.time() < end:
            if self.stop_flag:
                raise SessionError("用户已停止")
            self.c.wait_data(0.2)
            data = self.c.take()
            if data:
                acc += self.c.decode(data)
                last_data = time.time()
                end = max(end, time.time() + min(timeout, 10))  # 有输出就延长
                tail = acc[-200:]
                if auto_more and MORE_RE.search(tail[-60:]):
                    self.c.write(" ")
                    continue
                if auto_confirm and CONFIRM_RE.search(tail[-80:]):
                    self.c.write("Y\r")
                    continue
                fam, p = self._match_prompt(acc)
                if fam:
                    # 稍等片刻确认后续没有更多输出
                    time.sleep(0.15)
                    more = self.c.take()
                    if more:
                        acc += self.c.decode(more)
                        continue
                    self.family, self.prompt = fam, p
                    return acc
            elif idle_timeout and time.time() - last_data > idle_timeout:
                break
        raise SessionError("等待提示符超时。最后输出:\n" + _render_terminal(acc[-500:]))

    def send(self, cmd, timeout=15, auto_confirm=False):
        self.c.clear()
        self.c.write(cmd + "\r")
        return self.read_until_prompt(timeout=timeout, auto_confirm=auto_confirm)

    def run(self, cmd, timeout=60):
        """执行命令并返回干净的输出文本（去掉回显和提示符）。"""
        raw = self.send(cmd, timeout=timeout)
        text = _render_terminal(raw)
        lines = text.split("\n")
        for i, ln in enumerate(lines[:5]):
            if cmd.strip() in ln:
                lines = lines[i + 1:]
                break
        while lines and (not lines[-1].strip() or self._match_prompt(lines[-1])[0]):
            lines.pop()
        return "\n".join(lines)

    # ---------- 登录 ----------
    def login(self, username="", password="", enable_password="", ask=None, max_rounds=25):
        """唤醒并登录。ask(prompt_text) -> str 用于向用户询问未知交互。"""
        self.c.clear()
        self.c.write("\r")
        acc = ""
        sent_user = sent_pass = 0
        for _ in range(max_rounds):
            if self.stop_flag:
                raise SessionError("用户已停止")
            t_end = time.time() + 3
            while time.time() < t_end:
                self.c.wait_data(0.2)
                d = self.c.take()
                if d:
                    acc += self.c.decode(d)
                    t_end = max(t_end, time.time() + 0.8)
            view = _render_terminal(acc[-600:])
            tail = view.rstrip()[-200:]
            tl = tail.lower()
            fam, p = self._match_prompt(acc)
            if fam and not re.search(r"(username|login|password)\s*:\s*$", tl):
                self.family, self.prompt = fam, p
                self.log(f"已进入命令行，提示符: {p}")
                return
            acc_keep = acc
            acc = ""
            if MORE_RE.search(tail):
                self.c.write(" ")
            elif re.search(r"automatic configuration.*(ctrl_c|ctrl_d|ctrl\+c|ctrl\+d)", view, re.I | re.S) \
                    and "ctrl" in tl:
                self.log("检测到自动配置(Auto-Config)，发送 Ctrl+D 中止")
                self.c.write_raw(b"\x04")
            elif re.search(r"(stop auto-config|auto-config.*\[y/n\])", tl, re.I):
                self.log("检测到华为 Auto-Config，发送 y 中止")
                self.c.write("y\r")
            elif re.search(r"(dialog for setting up|setup.*\[yes/no\]|initial configuration dialog)", tl):
                self.c.write("no\r")
            elif re.search(r"(change (the )?password|modify the password|change now).*\[y/n\]", tl):
                self.c.write("n\r")
            elif re.search(r"(username|user name|login)\s*:\s*$", tl):
                if not username:
                    username = ask("交换机要求输入用户名:") if ask else ""
                if sent_user > 2:
                    raise SessionError("用户名/密码登录失败次数过多")
                sent_user += 1
                self.c.write(username + "\r")
            elif re.search(r"password\s*:\s*$", tl):
                if re.search(r"(new password|confirm|configure the login password|retype)", tl):
                    pw = ask("交换机要求设置/确认新登录密码(首次登录)，请输入:") if ask else ""
                    if not pw:
                        raise SessionError("设备要求设置首次登录密码，请在终端页手动完成")
                    self.c.write(pw + "\r")
                else:
                    if not password:
                        password = ask("交换机要求输入密码:") if ask else ""
                    if sent_pass > 2:
                        raise SessionError("密码错误次数过多")
                    sent_pass += 1
                    self.c.write(password + "\r")
            elif re.search(r"press (return|enter)", tl):
                self.c.write("\r")
            elif CONFIRM_RE.search(tail):
                ans = ask(f"设备提问:\n{tail}\n请输入回答(如 y/n):") if ask else "n"
                self.c.write((ans or "n") + "\r")
            else:
                acc = acc_keep[-300:]
                self.c.write("\r")
        raise SessionError("无法进入交换机命令行，请检查串口、波特率或在终端页手动登录后重试")

    def prepare(self, enable_password="", ask=None):
        """识别厂商、退回用户视图/特权模式、关闭分页。"""
        if self.family == "comware":
            if self.prompt.startswith("["):
                self.send("return")
            ver = self.run("display version", timeout=30)
            vl = ver.lower()
            if "huawei" in vl or "vrp" in vl:
                self.vendor = "huawei"
            else:
                self.vendor = "h3c"
            if self.vendor == "huawei":
                self.send("screen-length 0 temporary")
            else:
                self.send("screen-length disable")
            try:
                self.send("undo terminal monitor", timeout=5)
            except SessionError:
                pass
        else:
            if "(config" in self.prompt:
                self.send("end")
            if self.prompt.endswith(">"):
                self.c.clear()
                self.c.write("enable\r")
                time.sleep(1)
                txt = self.c.peek_text()
                if re.search(r"password\s*:\s*$", txt, re.I):
                    if not enable_password and ask:
                        enable_password = ask("请输入 enable 特权密码:")
                    self.c.clear()
                    self.c.write((enable_password or "") + "\r")
                self.read_until_prompt(timeout=10)
                if not self.prompt.endswith("#"):
                    raise SessionError("进入特权模式(enable)失败，请检查 enable 密码")
            ver = self.run("show version", timeout=30)
            vl = ver.lower()
            self.vendor = "ruijie" if ("ruijie" in vl or "rgos" in vl or "red-giant" in vl) else \
                ("cisco" if "cisco" in vl else "ruijie")
            self.send("terminal length 0")
        self.log(f"识别厂商: {self.vendor}")
        return self.vendor

    def get_config(self):
        if self.family == "comware":
            return self.run("display current-configuration", timeout=180)
        return self.run("show running-config", timeout=180)

    def get_interfaces(self):
        if self.vendor in ("h3c", "huawei"):
            return self.run("display interface brief", timeout=60)
        if self.vendor == "ruijie":
            return self.run("show interface status", timeout=60)
        return self.run("show interfaces status", timeout=60)

    # ---------- 下发 ----------
    def enter_config(self):
        if self.family == "comware":
            self.send("system-view")
        else:
            self.send("configure terminal")

    def exit_config(self):
        if self.family == "comware":
            self.send("return")
        else:
            self.send("end")

    def push(self, lines, on_progress=None, stop_on_error=False, line_timeout=20):
        """逐行下发命令。返回 [(line, ok, output)]。"""
        results = []
        self.enter_config()
        total = len(lines)
        for idx, line in enumerate(lines):
            if self.stop_flag:
                raise SessionError("用户已停止")
            cmd = line.strip()
            if not cmd or cmd.startswith("#") or cmd.startswith("!"):
                continue
            try:
                raw = self.send(cmd, timeout=line_timeout, auto_confirm=True)
            except SessionError as e:
                results.append((cmd, False, str(e)))
                if on_progress:
                    on_progress(idx + 1, total, cmd, False, str(e))
                if stop_on_error:
                    break
                continue
            out = _render_terminal(raw)
            body = "\n".join(out.split("\n")[1:-1]).strip()
            ok = not ERROR_PATTERNS.search(body)
            results.append((cmd, ok, body))
            if on_progress:
                on_progress(idx + 1, total, cmd, ok, body)
            if not ok and stop_on_error:
                break
        try:
            self.exit_config()
        except SessionError:
            pass
        return results

    def save(self):
        if self.vendor == "h3c":
            out = self.send("save force", timeout=120, auto_confirm=True)
        elif self.vendor == "huawei":
            out = self.send("save", timeout=120, auto_confirm=True)
        elif self.vendor == "ruijie":
            out = self.send("write", timeout=120, auto_confirm=True)
        else:
            out = self.send("write memory", timeout=120, auto_confirm=True)
        return _render_terminal(out)
