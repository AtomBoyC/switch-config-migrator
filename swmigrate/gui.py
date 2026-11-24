"""图形界面（tkinter，Windows / macOS 通用）。"""
import csv
import datetime
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import VENDOR_NAMES, VENDORS, __version__
from .console import SerialConsole, SessionError, SwitchSession, list_serial_ports
from .convert import DEFAULT_TEMPLATES, auto_map, build_target
from .generators import generate
from .ifname import canonical, extract_physical_ports, sort_key
from .parsers import detect_vendor_from_config, parse_config

WORK_DIR = Path(os.environ.get("SWMIGRATE_HOME") or (Path.home() / "SwitchMigrator"))
NO_MAP = "(不迁移)"
VENDOR_CHOICES = [f"{k} - {VENDOR_NAMES[k]}" for k in VENDORS]


def vendor_key(choice):
    return choice.split(" - ")[0].strip() if choice else ""


def now_tag():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def read_text_file(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"交换机配置迁移工具 v{__version__}  —  旧交换机配置一键导入新交换机")
        self.geometry("1180x780")
        self.minsize(980, 640)
        WORK_DIR.mkdir(parents=True, exist_ok=True)

        self.q = queue.Queue()
        self.console = None
        self.console_key = None
        self.session = None
        self.busy = False

        self.src_text = ""
        self.src_cfg = None
        self.new_ports = []
        self.new_vendor_detected = None
        self.mapping = {}
        self.target_cfg = None
        self.push_results = []

        self._build_ui()
        self.refresh_ports()
        self.after(50, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            if os.name == "nt":
                style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("", 11, "bold"), padding=6)

        # ---- 串口参数栏
        bar = ttk.LabelFrame(self, text="串口连接参数（Console 线）")
        bar.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(bar, text="串口:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(bar, textvariable=self.port_var, width=34)
        self.port_cb.grid(row=0, column=1, padx=2)
        ttk.Button(bar, text="刷新", width=5, command=self.refresh_ports).grid(row=0, column=2, padx=2)
        ttk.Label(bar, text="波特率:").grid(row=0, column=3, padx=4, sticky="e")
        self.baud_var = tk.StringVar(value="9600")
        ttk.Combobox(bar, textvariable=self.baud_var, width=8,
                     values=["9600", "19200", "38400", "57600", "115200"]).grid(row=0, column=4)
        ttk.Label(bar, text="编码:").grid(row=0, column=5, padx=4, sticky="e")
        self.enc_var = tk.StringVar(value="gbk")
        ttk.Combobox(bar, textvariable=self.enc_var, width=7, values=["gbk", "utf-8"]).grid(row=0, column=6)
        ttk.Label(bar, text="用户名:").grid(row=0, column=7, padx=4, sticky="e")
        self.user_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.user_var, width=12).grid(row=0, column=8)
        ttk.Label(bar, text="密码:").grid(row=0, column=9, padx=4, sticky="e")
        self.pass_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.pass_var, width=12, show="*").grid(row=0, column=10)
        ttk.Label(bar, text="enable密码:").grid(row=0, column=11, padx=4, sticky="e")
        self.enpass_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.enpass_var, width=10, show="*").grid(row=0, column=12, padx=(0, 4))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self._tab_old()
        self._tab_new()
        self._tab_map()
        self._tab_preview()
        self._tab_push()
        self._tab_term()

        self.status_var = tk.StringVar(value=f"就绪。备份/日志目录: {WORK_DIR}")
        st = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken")
        st.pack(fill="x", side="bottom")

    def _text(self, parent, **kw):
        frame = ttk.Frame(parent)
        txt = tk.Text(frame, wrap="none", undo=True, font=("Consolas" if os.name == "nt" else "Menlo", 10),
                      **kw)
        ys = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        txt.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, txt

    # ---- ① 旧交换机
    def _tab_old(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" ① 读取旧交换机配置 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        ttk.Label(top, text="把 Console 线接到【旧交换机】，然后点击：").pack(side="left", padx=6)
        ttk.Button(top, text="连接并读取旧交换机配置", style="Big.TButton",
                   command=self.read_old).pack(side="left", padx=4)
        ttk.Button(top, text="从文件导入配置…", command=self.import_old_file).pack(side="left", padx=4)
        ttk.Button(top, text="另存为…", command=self.save_old_file).pack(side="left", padx=4)
        ttk.Label(top, text="  源厂商:").pack(side="left")
        self.src_vendor_var = tk.StringVar(value="自动识别")
        ttk.Combobox(top, textvariable=self.src_vendor_var, width=24, state="readonly",
                     values=["自动识别"] + VENDOR_CHOICES).pack(side="left", padx=4)
        ttk.Button(top, text="重新解析", command=self.parse_old).pack(side="left", padx=4)
        self.old_info = tk.StringVar(value="尚未读取")
        ttk.Label(t, textvariable=self.old_info, foreground="#0a5").pack(fill="x", padx=8)
        f, self.old_txt = self._text(t)
        f.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- ② 新交换机
    def _tab_new(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" ② 读取新交换机端口 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        ttk.Label(top, text="新交换机厂商:").pack(side="left", padx=6)
        self.dst_vendor_var = tk.StringVar(value=VENDOR_CHOICES[2])
        ttk.Combobox(top, textvariable=self.dst_vendor_var, width=24, state="readonly",
                     values=VENDOR_CHOICES).pack(side="left")
        ttk.Label(top, text="   把 Console 线换到【新交换机】，然后点击：").pack(side="left")
        ttk.Button(top, text="连接新交换机并读取端口列表", style="Big.TButton",
                   command=self.read_new).pack(side="left", padx=4)

        mid = ttk.Frame(t)
        mid.pack(fill="x", padx=6)
        ttk.Label(mid, text="离线模式（不连接新设备）端口编号模板，{n} 为旧端口末位编号:").pack(side="left")
        self.tpl_var = tk.StringVar(value=DEFAULT_TEMPLATES["ruijie"])
        ttk.Entry(mid, textvariable=self.tpl_var, width=12).pack(side="left", padx=4)
        ttk.Button(mid, text="用下面粘贴的端口列表", command=self.use_pasted_ports).pack(side="left", padx=8)
        self.dst_vendor_var.trace_add("write", lambda *a: self.tpl_var.set(
            DEFAULT_TEMPLATES.get(vendor_key(self.dst_vendor_var.get()), "0/{n}")))

        self.new_info = tk.StringVar(value="尚未读取新交换机端口（也可以把 display interface brief / "
                                           "show interface status 的输出粘贴到下面，再点“用下面粘贴的端口列表”）")
        ttk.Label(t, textvariable=self.new_info, foreground="#0a5").pack(fill="x", padx=8, pady=4)
        f, self.new_txt = self._text(t)
        f.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- ③ 端口映射
    def _tab_map(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" ③ 端口映射 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text="自动映射", command=self.do_auto_map).pack(side="left", padx=6)
        ttk.Button(top, text="导出映射表 CSV", command=self.export_map).pack(side="left", padx=4)
        ttk.Button(top, text="导入映射表 CSV", command=self.import_map).pack(side="left", padx=4)
        self.only_cfg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="只显示有配置的端口", variable=self.only_cfg_var,
                        command=self.fill_map_tree).pack(side="left", padx=10)
        ttk.Label(top, text="提示：双击“新端口”一列可修改映射", foreground="#777").pack(side="left", padx=10)
        ttk.Button(top, text="下一步：生成配置 →", command=self.generate_cfg).pack(side="right", padx=6)

        cols = ("old", "desc", "mode", "lag", "new")
        frame = ttk.Frame(t)
        frame.pack(fill="both", expand=True, padx=6, pady=4)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for c, txt, w in [("old", "旧端口", 220), ("desc", "描述", 220), ("mode", "模式 / VLAN", 300),
                          ("lag", "聚合组", 70), ("new", "新端口（双击修改）", 240)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ys.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self.tree.tag_configure("unmapped", background="#ffe0e0")
        self.tree.tag_configure("empty", foreground="#999")
        self.tree.bind("<Double-1>", self._edit_map_cell)
        self._map_editor = None

    # ---- ④ 预览
    def _tab_preview(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" ④ 预览 / 编辑配置 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text="重新生成配置", command=self.generate_cfg).pack(side="left", padx=6)
        ttk.Button(top, text="导出配置到文件…", command=self.export_cfg).pack(side="left", padx=4)
        ttk.Label(top, text="（可直接在左侧编辑要下发的命令，下发时以左侧内容为准）",
                  foreground="#777").pack(side="left", padx=6)
        ttk.Button(top, text="下一步：一键下发 →", command=lambda: self.nb.select(4)).pack(side="right", padx=6)
        pw = ttk.PanedWindow(t, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=6, pady=4)
        f1, self.cfg_txt = self._text(pw)
        pw.add(f1, weight=3)
        right = ttk.Frame(pw)
        pw.add(right, weight=2)
        ttk.Label(right, text="⚠ 提示 / 需人工处理的项目：", foreground="#c60").pack(anchor="w")
        f2, self.warn_txt = self._text(right, height=12)
        self.warn_txt.configure(wrap="word")
        f2.pack(fill="both", expand=True)
        ttk.Label(right, text="未自动转换的原始配置（请对照手动配置）：", foreground="#c60").pack(anchor="w")
        f3, self.unparsed_txt = self._text(right, height=12)
        f3.pack(fill="both", expand=True)

    # ---- ⑤ 下发
    def _tab_push(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" ⑤ 一键下发到新交换机 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        self.stop_err_var = tk.BooleanVar(value=False)
        self.save_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="遇到错误立即停止", variable=self.stop_err_var).pack(side="left", padx=6)
        ttk.Checkbutton(top, text="下发完成后保存配置(save/write)", variable=self.save_var).pack(side="left", padx=6)
        ttk.Label(top, text="单条命令超时(秒):").pack(side="left", padx=(12, 2))
        self.timeout_var = tk.StringVar(value="20")
        ttk.Entry(top, textvariable=self.timeout_var, width=5).pack(side="left")
        self.push_btn = ttk.Button(top, text="▶ 开始一键下发", style="Big.TButton", command=self.push)
        self.push_btn.pack(side="left", padx=16)
        ttk.Button(top, text="■ 停止", command=self.stop).pack(side="left", padx=4)
        ttk.Button(top, text="导出下发报告…", command=self.export_report).pack(side="left", padx=8)
        self.prog = ttk.Progressbar(t, mode="determinate")
        self.prog.pack(fill="x", padx=8, pady=4)
        self.push_info = tk.StringVar(value="")
        ttk.Label(t, textvariable=self.push_info).pack(fill="x", padx=8)
        f, self.push_txt = self._text(t)
        f.pack(fill="both", expand=True, padx=6, pady=6)
        self.push_txt.tag_configure("ok", foreground="#080")
        self.push_txt.tag_configure("err", foreground="#d00", background="#fff0f0")
        self.push_txt.tag_configure("info", foreground="#06c")

    # ---- 终端
    def _tab_term(self):
        t = ttk.Frame(self.nb)
        self.nb.add(t, text=" 串口终端 ")
        top = ttk.Frame(t)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text="打开串口", command=self.open_term).pack(side="left", padx=6)
        ttk.Button(top, text="关闭串口", command=self.close_console).pack(side="left", padx=4)
        ttk.Button(top, text="发送 Ctrl+C", command=lambda: self._term_raw(b"\x03")).pack(side="left", padx=4)
        ttk.Button(top, text="发送 Ctrl+D", command=lambda: self._term_raw(b"\x04")).pack(side="left", padx=4)
        ttk.Button(top, text="发送空格(翻页)", command=lambda: self._term_raw(b" ")).pack(side="left", padx=4)
        ttk.Button(top, text="清屏", command=lambda: self.term_txt.delete("1.0", "end")).pack(side="left", padx=4)
        ttk.Label(top, text="（自动流程卡住时，可在这里手动操作设备，如首次登录改密码）",
                  foreground="#777").pack(side="left", padx=6)
        f, self.term_txt = self._text(t)
        self.term_txt.configure(background="#111", foreground="#ddd", insertbackground="#fff")
        f.pack(fill="both", expand=True, padx=6, pady=4)
        bot = ttk.Frame(t)
        bot.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(bot, text="输入命令:").pack(side="left")
        self.term_in = ttk.Entry(bot)
        self.term_in.pack(side="left", fill="x", expand=True, padx=4)
        self.term_in.bind("<Return>", self._term_send)
        ttk.Button(bot, text="发送(回车)", command=self._term_send).pack(side="left")

    # ================================================================ 串口
    def refresh_ports(self):
        ports = list_serial_ports()
        self.port_cb["values"] = [p[1] for p in ports]
        if ports and not self.port_var.get():
            self.port_var.set(ports[0][1])
        if not ports:
            self.status_var.set("未发现串口。请检查 USB 转串口驱动是否安装（常见芯片: CH340 / PL2303 / FTDI）")

    def _port_device(self):
        v = self.port_var.get().strip()
        return v.split()[0] if v else ""

    def ensure_console(self):
        dev = self._port_device()
        if not dev:
            raise SessionError("请先选择串口")
        key = (dev, int(self.baud_var.get()), self.enc_var.get())
        if self.console and self.console_key == key:
            return self.console
        self.close_console()
        self.console = SerialConsole(dev, key[1], key[2], on_data=lambda s: self.q.put(("term", s)))
        self.console_key = key
        self.log_status(f"已打开串口 {dev} @ {key[1]}")
        return self.console

    def close_console(self):
        if self.console:
            self.console.close()
            self.console = None
            self.console_key = None
            self.log_status("串口已关闭")

    def open_term(self):
        try:
            self.ensure_console()
            self.console.write("\r")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _term_send(self, event=None):
        if not self.console:
            self.open_term()
            if not self.console:
                return
        self.console.write(self.term_in.get() + "\r")
        self.term_in.delete(0, "end")

    def _term_raw(self, b):
        if self.console:
            self.console.write_raw(b)

    # ================================================================ 线程 / 队列
    def log_status(self, msg):
        self.q.put(("status", msg))

    def ask_user(self, prompt, secret=True):
        """工作线程中调用：在主线程弹框询问并等待。"""
        ev = threading.Event()
        box = {}
        self.q.put(("ask", prompt, secret, ev, box))
        ev.wait()
        return box.get("v") or ""

    def run_bg(self, fn, done=None):
        if self.busy:
            messagebox.showwarning("请稍候", "有任务正在进行中")
            return
        self.busy = True

        def worker():
            try:
                res = fn()
                self.q.put(("done", done, res, None))
            except Exception as e:
                self.q.put(("done", done, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        term_buf = []
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "term":
                    term_buf.append(item[1])
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "ask":
                    _, prompt, secret, ev, box = item
                    box["v"] = simpledialog.askstring("设备交互", prompt, show="*" if secret else None,
                                                      parent=self)
                    ev.set()
                elif kind == "push":
                    self._on_push_progress(*item[1:])
                elif kind == "done":
                    _, cb, res, err = item
                    self.busy = False
                    if err:
                        self.status_var.set(f"失败: {err}")
                        messagebox.showerror("出错了", f"{err}\n\n可切换到“串口终端”页查看设备实际输出。")
                    elif cb:
                        cb(res)
        except queue.Empty:
            pass
        if term_buf:
            txt = "".join(term_buf).replace("\r\n", "\n").replace("\r", "")
            self.term_txt.insert("end", txt)
            if int(self.term_txt.index("end-1c").split(".")[0]) > 20000:
                self.term_txt.delete("1.0", "5000.0")
            self.term_txt.see("end")
        self.after(50, self._poll_queue)

    def _new_session(self):
        con = self.ensure_console()
        s = SwitchSession(con, log=self.log_status)
        self.session = s
        s.login(self.user_var.get(), self.pass_var.get(), self.enpass_var.get(),
                ask=lambda p: self.ask_user(p, secret="密码" in p))
        s.prepare(self.enpass_var.get(), ask=lambda p: self.ask_user(p))
        return s

    def stop(self):
        if self.session:
            self.session.stop_flag = True
            self.log_status("正在停止…")

    # ================================================================ ① 旧交换机
    def read_old(self):
        def job():
            s = self._new_session()
            self.log_status(f"正在读取 {s.vendor} 配置（设备较大时可能需要 1~2 分钟）…")
            cfg = s.get_config()
            if len(cfg.strip()) < 50:
                raise SessionError("读取到的配置为空，请重试")
            path = WORK_DIR / f"backup_{s.vendor}_{now_tag()}.cfg"
            path.write_text(cfg, encoding="utf-8")
            return s.vendor, cfg, path

        def done(res):
            vendor, cfg, path = res
            self.src_vendor_var.set(f"{vendor} - {VENDOR_NAMES[vendor]}")
            self._set_old_text(cfg)
            self.status_var.set(f"旧交换机配置已读取并备份到 {path}")
            messagebox.showinfo("完成", f"已读取旧交换机配置（{vendor}），并已自动备份到:\n{path}\n\n"
                                        f"请把 Console 线换到新交换机，进入第②步。")
            self.nb.select(1)

        self.run_bg(job, done)

    def import_old_file(self):
        p = filedialog.askopenfilename(title="选择旧交换机配置文件",
                                       filetypes=[("配置文件", "*.cfg *.txt *.log *.conf"), ("所有文件", "*")])
        if not p:
            return
        self._set_old_text(read_text_file(p))

    def save_old_file(self):
        p = filedialog.asksaveasfilename(defaultextension=".cfg", initialfile=f"old_{now_tag()}.cfg")
        if p:
            Path(p).write_text(self.old_txt.get("1.0", "end-1c"), encoding="utf-8")

    def _set_old_text(self, text):
        self.old_txt.delete("1.0", "end")
        self.old_txt.insert("1.0", text)
        self.parse_old()

    def parse_old(self):
        text = self.old_txt.get("1.0", "end-1c")
        if not text.strip():
            return
        sv = vendor_key(self.src_vendor_var.get()) if self.src_vendor_var.get() != "自动识别" else ""
        vendor = sv or detect_vendor_from_config(text)
        if not vendor:
            messagebox.showwarning("无法识别", "无法自动识别配置所属厂商，请手动选择“源厂商”后点“重新解析”")
            return
        try:
            self.src_cfg = parse_config(text, vendor)
        except Exception as e:
            messagebox.showerror("解析失败", str(e))
            return
        c = self.src_cfg
        nconf = sum(1 for i in c.interfaces.values() if i.has_config)
        self.old_info.set(
            f"厂商: {VENDOR_NAMES[vendor]}   主机名: {c.hostname or '-'}   VLAN: {len(c.vlans)} 个   "
            f"物理端口: {len(c.interfaces)} 个（有配置 {nconf} 个）   聚合口: {len(c.lags)}   "
            f"VLAN 接口: {len(c.svis)}   静态路由: {len(c.routes)}   OSPF: {len(c.ospf)}   "
            f"未识别配置段: {len(c.unparsed_blocks)}")
        self.do_auto_map(silent=True)

    # ================================================================ ② 新交换机
    def read_new(self):
        def job():
            s = self._new_session()
            out = s.get_interfaces()
            return s.vendor, out

        def done(res):
            vendor, out = res
            self.new_vendor_detected = vendor
            if vendor_key(self.dst_vendor_var.get()) != vendor:
                self.dst_vendor_var.set(f"{vendor} - {VENDOR_NAMES[vendor]}")
            self.new_txt.delete("1.0", "end")
            self.new_txt.insert("1.0", out)
            self.use_pasted_ports()
            if self.new_ports:
                self.nb.select(2)

        self.run_bg(job, done)

    def use_pasted_ports(self):
        text = self.new_txt.get("1.0", "end-1c")
        ports = extract_physical_ports(text)
        vendor = vendor_key(self.dst_vendor_var.get())
        self.new_ports = sorted({canonical(p, vendor) for p in ports}, key=sort_key)
        kinds = {}
        for p in self.new_ports:
            from .ifname import parse_ifname_any
            k = parse_ifname_any(p)[0]
            kinds[k] = kinds.get(k, 0) + 1
        self.new_info.set(f"新交换机({vendor}) 物理端口 {len(self.new_ports)} 个: " +
                          "，".join(f"{k} × {v}" for k, v in kinds.items()))
        self.do_auto_map(silent=True)

    # ================================================================ ③ 映射
    def do_auto_map(self, silent=False):
        if not self.src_cfg:
            if not silent:
                messagebox.showwarning("提示", "请先在第①步读取或导入旧交换机配置")
            return
        vendor = vendor_key(self.dst_vendor_var.get())
        self.mapping = auto_map(self.src_cfg, vendor, self.new_ports or None, self.tpl_var.get())
        self.fill_map_tree()

    def _mode_desc(self, itf):
        from .util import vlan_str_dash
        if itf.mode == "access":
            return f"access vlan {itf.access_vlan or 1}"
        if itf.mode == "trunk":
            a = "默认" if itf.allowed is None else vlan_str_dash(itf.allowed)
            if len(a) > 40:
                a = a[:40] + "…"
            return f"trunk pvid {itf.native_vlan or 1} 允许 {a}"
        if itf.mode == "hybrid":
            return f"hybrid pvid {itf.native_vlan or 1} untag {vlan_str_dash(itf.hybrid_untagged)}" \
                   f" tag {vlan_str_dash(itf.hybrid_tagged)}"
        if itf.mode == "routed":
            return "三层口 " + ", ".join(ip for ip, _, _ in itf.ips)
        return "shutdown" if itf.shutdown else "(默认配置)"

    def fill_map_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.src_cfg:
            return
        for itf in self.src_cfg.physical_sorted():
            if self.only_cfg_var.get() and not itf.has_config:
                continue
            new = self.mapping.get(itf.name, "")
            tags = []
            if not new and itf.has_config:
                tags.append("unmapped")
            elif not itf.has_config:
                tags.append("empty")
            self.tree.insert("", "end", iid=itf.name, tags=tags, values=(
                itf.name, itf.description, self._mode_desc(itf),
                itf.lag_id or "", new or NO_MAP))

    def _edit_map_cell(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        col = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if col != "#5" or not item:
            return
        x, y, w, h = self.tree.bbox(item, col)
        if self._map_editor:
            self._map_editor.destroy()
        vendor = vendor_key(self.dst_vendor_var.get())
        values = [NO_MAP] + (self.new_ports or sorted(set(v for v in self.mapping.values() if v), key=sort_key))
        cb = ttk.Combobox(self.tree, values=values)
        cb.set(self.mapping.get(item) or NO_MAP)
        cb.place(x=x, y=y, width=w, height=h)
        cb.focus_set()
        self._map_editor = cb

        def commit(e=None):
            v = cb.get().strip()
            if v == NO_MAP or not v:
                self.mapping[item] = ""
            else:
                self.mapping[item] = canonical(v, vendor)
            cb.destroy()
            self._map_editor = None
            self.fill_map_tree()
            if self.tree.exists(item):
                self.tree.see(item)

        cb.bind("<<ComboboxSelected>>", commit)
        cb.bind("<Return>", commit)
        cb.bind("<FocusOut>", commit)
        cb.bind("<Escape>", lambda e: (cb.destroy(), setattr(self, "_map_editor", None)))

    def export_map(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="port_mapping.csv")
        if not p:
            return
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["旧端口", "新端口"])
            for k, v in self.mapping.items():
                w.writerow([k, v])

    def import_map(self):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if not p:
            return
        vendor = vendor_key(self.dst_vendor_var.get())
        with open(p, newline="", encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0] in self.mapping:
                    self.mapping[row[0]] = canonical(row[1].strip(), vendor) if row[1].strip() else ""
        self.fill_map_tree()

    # ================================================================ ④ 生成
    def generate_cfg(self):
        if not self.src_cfg:
            messagebox.showwarning("提示", "请先在第①步读取或导入旧交换机配置")
            return
        vendor = vendor_key(self.dst_vendor_var.get())
        new, warns = build_target(self.src_cfg, self.mapping, vendor)
        self.target_cfg = new
        lines = generate(new, vendor)
        self.cfg_txt.delete("1.0", "end")
        self.cfg_txt.insert("1.0", "\n".join(lines))
        self.warn_txt.delete("1.0", "end")
        self.warn_txt.insert("1.0", "\n".join(f"• {w}" for w in warns) or "无")
        self.unparsed_txt.delete("1.0", "end")
        self.unparsed_txt.insert("1.0", "\n\n".join("\n".join(b) for b in new.unparsed_blocks) or "无")
        self.status_var.set(f"已生成 {vendor} 配置 {len(lines)} 行，提示 {len(warns)} 条，"
                            f"未转换配置段 {len(new.unparsed_blocks)} 个")
        self.nb.select(3)

    def export_cfg(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt",
                                         initialfile=f"new_{vendor_key(self.dst_vendor_var.get())}_{now_tag()}.txt")
        if p:
            Path(p).write_text(self.cfg_txt.get("1.0", "end-1c"), encoding="utf-8")

    # ================================================================ ⑤ 下发
    def push(self):
        lines = [ln for ln in self.cfg_txt.get("1.0", "end-1c").splitlines() if ln.strip()]
        if not lines:
            messagebox.showwarning("提示", "没有可下发的配置，请先在第④步生成配置")
            return
        vendor = vendor_key(self.dst_vendor_var.get())
        if not messagebox.askyesno(
                "确认下发",
                f"即将向【新交换机】下发 {len(lines)} 行 {VENDOR_NAMES[vendor]} 配置。\n\n"
                f"请确认：\n1. Console 线已连接到新交换机（不是旧交换机！）\n"
                f"2. 新交换机为空配置或可以被覆盖\n\n确定开始？"):
            return
        try:
            line_timeout = int(self.timeout_var.get())
        except ValueError:
            line_timeout = 20
        self.push_txt.delete("1.0", "end")
        self.prog["maximum"] = len(lines)
        self.prog["value"] = 0
        self.push_results = []
        stop_on_error = self.stop_err_var.get()
        do_save = self.save_var.get()

        def job():
            s = self._new_session()
            if s.vendor != vendor:
                ans = self.ask_user(
                    f"检测到当前连接的设备是 {s.vendor}，而生成的配置是 {vendor}。\n"
                    f"可能接错了设备！输入 yes 强制继续，其他任意内容取消:", secret=False)
                if ans.strip().lower() != "yes":
                    raise SessionError("已取消：设备厂商与配置不一致")
            self.q.put(("push", "info", f"已连接 {s.vendor} 设备，提示符 {s.prompt}，开始下发…"))
            res = s.push(lines, on_progress=lambda i, n, c, ok, out: self.q.put(("push", "line", i, n, c, ok, out)),
                         stop_on_error=stop_on_error, line_timeout=line_timeout)
            saved = None
            if do_save:
                if any(not ok for _, ok, _ in res):
                    ans = self.ask_user("有命令执行失败，是否仍然保存配置？输入 yes 保存:", secret=False)
                    if ans.strip().lower() != "yes":
                        return res, None
                self.q.put(("push", "info", "正在保存配置…"))
                saved = s.save()
            return res, saved

        def done(r):
            res, saved = r
            self.push_results = res
            fails = [x for x in res if not x[1]]
            report = WORK_DIR / f"push_report_{now_tag()}.txt"
            self._write_report(report)
            msg = f"下发完成：共 {len(res)} 条，成功 {len(res) - len(fails)} 条，失败 {len(fails)} 条。"
            if saved is not None:
                msg += "\n配置已保存。"
            msg += f"\n\n报告已保存到:\n{report}"
            self.push_info.set(msg.replace("\n", " "))
            (messagebox.showwarning if fails else messagebox.showinfo)("下发结果", msg)

        self.run_bg(job, done)

    def _on_push_progress(self, kind, *args):
        if kind == "info":
            self.push_txt.insert("end", args[0] + "\n", "info")
        else:
            i, n, cmd, ok, out = args
            self.prog["value"] = i
            self.push_info.set(f"进度 {i}/{n}: {cmd}")
            if ok:
                self.push_txt.insert("end", f"[OK ] {cmd}\n", "ok")
            else:
                self.push_txt.insert("end", f"[ERR] {cmd}\n      {out.strip()[:300]}\n", "err")
        self.push_txt.see("end")

    def _write_report(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"下发时间: {datetime.datetime.now()}\n")
            fails = [x for x in self.push_results if not x[1]]
            f.write(f"总计 {len(self.push_results)} 条，失败 {len(fails)} 条\n\n")
            if fails:
                f.write("==== 失败的命令 ====\n")
                for cmd, _, out in fails:
                    f.write(f"{cmd}\n    -> {out.strip()}\n")
                f.write("\n")
            f.write("==== 全部命令 ====\n")
            for cmd, ok, out in self.push_results:
                f.write(f"[{'OK' if ok else 'ERR'}] {cmd}\n")

    def export_report(self):
        if not self.push_results:
            messagebox.showinfo("提示", "还没有下发记录")
            return
        p = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f"push_report_{now_tag()}.txt")
        if p:
            self._write_report(p)

    def _on_close(self):
        if self.busy and not messagebox.askyesno("退出", "任务仍在进行，确定退出？"):
            return
        self.close_console()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
