# -*- coding: utf-8 -*-
"""
wcalert.py — 白名单消息拦截小窗（10 秒决策）

白名单里的人发来消息时，右下角弹一个小窗，给你三个选择：

   [查看]      → 打开微信，切到那个人的会话（你自己回）
   [自动回复]  → 工具按上下文 + 你的说话风格生成回复并发出
   [忽略]      → 什么都不做

10 秒内不选 = 忽略（不会偷偷替你发任何东西）。

样式（这一版是照着"小一点 + 暖色调"调的）：
  · 尺寸 330x150，右下角，不挡视线
  · **浅色暖调**：米白卡片 #FFFBF5 + 暖棕文字 #3A322B + 琥珀/陶土色按钮
    （深色版在浅色桌面上太突兀，暖白更耐看）
  · 顶部一条圆角琥珀色倒计时条，比数字更直观，数字也保留
  · 头像用暖色渐变（桃/杏/陶土色系），由昵称推导
  · 按钮是 Canvas 圆角矩形，带悬停变深 / 按下反馈
  · 淡入动画 + 超时先提示再淡出，不会凭空消失
"""

from __future__ import annotations

import colorsys
import threading
import time
import tkinter as tk
from collections import deque

# ---- 暖色浅调配色 ----
CARD = "#FFFBF5"        # 米白卡片
CARD_2 = "#FDF3E7"      # 消息气泡底（更暖一点）
LINE = "#EADFD0"        # 描边
TXT = "#3A322B"         # 主文字（暖棕黑）
TXT_2 = "#8A7A6A"       # 次要文字
TXT_3 = "#B3A697"       # 更弱的提示

AMBER = "#E8A33D"       # 琥珀（倒计时）
AMBER_D = "#C9861F"
CLAY = "#C96F4A"        # 陶土（主按钮：自动回复）
CLAY_D = "#A85636"
SKY = "#5B8DB8"         # 柔和蓝（查看）
SKY_D = "#457197"
STONE = "#EDE4D8"       # 灰暖（忽略）
STONE_D = "#DED2C2"
ROSE = "#C4573F"        # 紧迫

FONT = "Microsoft YaHei UI"
DEFAULT_TIMEOUT = 10
REPLY_TIMEOUT = 25


# ==========================================================================
# 视觉工具
# ==========================================================================

def warm_avatar(name: str) -> tuple:
    """暖色系头像渐变：把名字映射到桃/杏/陶土/蜂蜜的色相区间。"""
    h = 0
    for ch in (name or "?"):
        h = (h * 31 + ord(ch)) % 360
    # 收窄到暖色区间 20°~60°（橙黄到琥珀），再少量借 5°~20°（桃红）
    hue = 18 + (h % 48)
    c1 = _hsl(hue, 62, 62)
    c2 = _hsl((hue + 14) % 360, 58, 50)
    return f"#{c1}", f"#{c2}"


def _hsl(h: int, s: int, l: int) -> str:
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return f"{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def dwm_round_corners(win: tk.Toplevel) -> bool:
    """Win11 原生圆角。浅色卡片配圆角会好看很多。"""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        pref = ctypes.c_int(2)      # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        light = ctypes.c_int(0)     # 浅色标题栏，避免边缘发暗
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(light), ctypes.sizeof(light))
        return True
    except Exception:
        return False


class RoundedButton(tk.Canvas):
    """Canvas 圆角按钮（tkinter 原生 Button 在浅色暖调下会显得很土）。"""

    def __init__(self, parent, text, color, hover, command,
                 width=74, height=27, radius=8, text_color="#FFFFFF",
                 font_size=9):
        super().__init__(parent, width=width, height=height, bg=CARD,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.color, self.hover = color, hover
        self.enabled = True
        self._shape = round_rect(self, 1, 1, width - 1, height - 1, radius,
                                 fill=color, outline="")
        self._text = self.create_text(width / 2, height / 2, text=text,
                                      fill=text_color,
                                      font=(FONT, font_size, "bold"))
        self.bind("<Enter>", lambda e: self._set(self.hover))
        self.bind("<Leave>", lambda e: self._set(self.color))
        self.bind("<Button-1>", lambda e: self._set(self.hover))
        self.bind("<ButtonRelease-1>", self._release)

    def _set(self, color):
        if self.enabled:
            self.itemconfigure(self._shape, fill=color)

    def _release(self, event):
        if not self.enabled:
            return
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set(self.hover if inside else self.color)
        if inside and self.command:
            self.command()

    def set_enabled(self, on: bool):
        self.enabled = on
        self.itemconfigure(self._shape, fill=self.color if on else STONE)
        self.itemconfigure(self._text, fill="#FFFFFF" if on else TXT_3)

    def set_text(self, text):
        self.itemconfigure(self._text, text=text)


class Avatar(tk.Canvas):
    """小圆头像（32px）。"""

    def __init__(self, parent, name, size=32):
        super().__init__(parent, width=size, height=size, bg=CARD,
                         highlightthickness=0, bd=0)
        c1, c2 = warm_avatar(name)
        self.create_oval(0, 0, size, size, fill=c1, outline="")
        self.create_arc(size * 0.05, size * 0.05, size, size,
                        start=205, extent=145, fill=c2, outline="")
        self.create_text(size / 2, size / 2, text=(name or "?")[:1],
                         fill="#FFFFFF", font=(FONT, int(size * 0.44), "bold"))


class ProgressBar(tk.Canvas):
    """顶部倒计时条（矮一点，2px）。"""

    def __init__(self, parent, width, height=2, color=AMBER):
        super().__init__(parent, width=width, height=height, bg=CARD,
                         highlightthickness=0, bd=0)
        self.w, self.h, self.color = width, height, color
        self.track = self.create_rectangle(0, 0, width, height, fill="#F3E9DA", outline="")
        self.fill = round_rect(self, 0, 0, width, height, height / 2,
                               fill=color, outline="")

    def set_ratio(self, ratio, color=None):
        ratio = max(0.0, min(1.0, ratio))
        if color and color != self.color:
            self.color = color
            self.itemconfigure(self.fill, fill=color)
        w = max(2.0, self.w * ratio)
        h, r = self.h, min(self.h / 2, self.w * ratio / 2)
        self.coords(self.fill, r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
                    w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0)


# ==========================================================================
# 弹窗管理器
# ==========================================================================

class AlertManager:
    """串行弹窗：一次只显示一个，其余排队。"""

    def __init__(self, on_view=None, on_reply=None, on_ignore=None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.on_view, self.on_reply, self.on_ignore = on_view, on_reply, on_ignore
        self.timeout = timeout
        self.queue = deque()
        self.current = None
        self.seen = set()
        self.stats = {"shown": 0, "viewed": 0, "replied": 0, "ignored": 0,
                      "timeout": 0}

    def push(self, wxid: str, name: str, text: str, is_group: bool = False,
             fp: str = "", ctx: list | None = None) -> bool:
        """入队。重复消息返回 False。

        ctx 由调用方带进来——小窗自己不去读数据
        （读数据要初始化整个 Store，几十秒，小窗会卡死）。
        """
        key = fp or f"{wxid}:{text}"
        if key in self.seen:
            return False
        self.seen.add(key)
        if len(self.seen) > 500:
            self.seen = set(list(self.seen)[-250:])
        self.queue.append({"wxid": wxid, "name": name, "text": text,
                           "is_group": is_group, "fp": key, "t": time.time(),
                           "ctx": ctx or []})
        return True

    def run(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.after(150, self._next)
        self.root.mainloop()

    def stop(self):
        try:
            if self.current:
                self.current.finish("ignored")
            self.root.quit()
        except Exception:
            pass

    def _next(self):
        if self.current is None and self.queue:
            self.current = AlertWindow(self.root, self.queue.popleft(),
                                       self.timeout, on_done=self._done)
            self.stats["shown"] += 1
        self.root.after(150, self._next)

    def _done(self, action: str, item: dict):
        self.current = None
        self.stats[action] = self.stats.get(action, 0) + 1
        try:
            if action == "viewed" and self.on_view:
                self.on_view(item)
            elif action == "replied" and self.on_reply:
                self.on_reply(item)
            elif action in ("ignored", "timeout") and self.on_ignore:
                self.on_ignore(item, action)
        except Exception as e:
            print(f"[alert] 回调异常: {type(e).__name__}: {e}")


# ==========================================================================
# 单个小窗
# ==========================================================================

class AlertWindow:
    W, H = 330, 150

    def __init__(self, root: tk.Tk, item: dict, timeout: int, on_done):
        self.root, self.item, self.on_done = root, item, on_done
        self.total = timeout
        self.remaining = float(timeout)
        self.finished = self.busy = self.work_done = False

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.win.configure(bg=CARD)

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{self.W}x{self.H}+{sw - self.W - 22}+{sh - self.H - 58}")
        self.native_round = dwm_round_corners(self.win)

        # 1px 暖描边
        outer = tk.Frame(self.win, bg=LINE)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        card = tk.Frame(outer, bg=CARD)
        card.pack(fill="both", expand=True)

        self.bar = ProgressBar(card, self.W - 2, 2, AMBER)
        self.bar.pack(fill="x")

        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(9, 10))

        # ---- 头部 ----
        head = tk.Frame(body, bg=CARD)
        head.pack(fill="x")
        Avatar(head, item.get("name", "?"), 32).pack(side="left", padx=(0, 9))

        meta = tk.Frame(head, bg=CARD)
        meta.pack(side="left", fill="x", expand=True)
        row = tk.Frame(meta, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text=item.get("name", "?")[:14], bg=CARD, fg=TXT,
                 font=(FONT, 10, "bold")).pack(side="left")
        if item.get("is_group"):
            tk.Label(row, text="群", bg=CARD_2, fg=TXT_2,
                     font=(FONT, 7), padx=4).pack(side="left", padx=4)
        tk.Label(meta, text="发来新消息", bg=CARD, fg=TXT_3,
                 font=(FONT, 8)).pack(anchor="w")

        cd = tk.Frame(head, bg=CARD)
        cd.pack(side="right")
        self.countdown = tk.Label(cd, text=str(timeout), bg=CARD, fg=AMBER,
                                  font=(FONT, 13, "bold"))
        self.countdown.pack()
        tk.Label(cd, text="s", bg=CARD, fg=TXT_3, font=(FONT, 7)).pack()

        # ---- 消息（单行省略，小窗不占地方）----
        text = (item.get("text") or "").strip().replace("\n", " ")
        if len(text) > 42:
            text = text[:42] + "…"
        bubble = tk.Frame(body, bg=CARD_2)
        bubble.pack(fill="x", pady=(8, 0))
        tk.Label(bubble, text=text, bg=CARD_2, fg=TXT, anchor="w",
                 font=(FONT, 9), padx=9, pady=6).pack(fill="x")

        # ---- 状态行 ----
        self.status = tk.Label(body, text="", bg=CARD, fg=CLAY, anchor="w",
                               font=(FONT, 8), wraplength=self.W - 34)
        self.status.pack(fill="x", pady=(4, 0))

        # ---- 按钮 ----
        btns = tk.Frame(body, bg=CARD)
        btns.pack(fill="x", pady=(6, 0), side="bottom")
        self.btn_view = RoundedButton(btns, "查看  V", SKY, SKY_D,
                                      self.do_view, width=70, height=26)
        self.btn_view.pack(side="left")
        self.btn_reply = RoundedButton(btns, "自动回复  R", CLAY, CLAY_D,
                                       self.do_reply, width=100, height=26)
        self.btn_reply.pack(side="left", padx=6)
        self.btn_ignore = RoundedButton(btns, "忽略", STONE, STONE_D,
                                        self.do_ignore, width=56, height=26,
                                        text_color=TXT_2)
        self.btn_ignore.pack(side="right")

        for k, fn in (("v", self.do_view), ("V", self.do_view),
                      ("r", self.do_reply), ("R", self.do_reply)):
            self.win.bind(f"<Key-{k}>", lambda e, f=fn: f())
        self.win.bind("<Escape>", lambda e: self.do_ignore())
        self.win.focus_force()
        self.win.after(60, lambda: self.win.focus_force())

        self._fade_in()
        self.win.after(100, self._tick)

    # -------- 动画 --------

    def _fade_in(self, a=0.0):
        if self.finished:
            return
        a = min(0.98, a + 0.14)
        try:
            self.win.attributes("-alpha", a)
        except Exception:
            return
        if a < 0.98:
            self.win.after(12, lambda: self._fade_in(a))

    def _fade_out(self, then=None, a=0.98):
        if a <= 0.06:
            try:
                self.win.attributes("-alpha", 0.0)
            except Exception:
                pass
            if then:
                then()
            return
        a -= 0.18
        try:
            self.win.attributes("-alpha", max(0.0, a))
        except Exception:
            pass
        self.win.after(10, lambda: self._fade_out(then, a))

    # -------- 倒计时 --------

    def _tick(self):
        if self.finished or self.busy:
            return
        self.remaining -= 0.1
        urgent = self.remaining <= 3.5
        self.bar.set_ratio(self.remaining / self.total, ROSE if urgent else AMBER)
        self.countdown.configure(text=str(max(0, int(self.remaining + 0.999))),
                                 fg=ROSE if urgent else AMBER)
        if self.remaining <= 0:
            self.status.configure(text="超时，已忽略", fg=TXT_3)
            self._fade_out(lambda: self.finish("timeout"))
            return
        self.win.after(100, self._tick)

    # -------- 动作 --------

    def finish(self, action: str):
        if self.finished:
            return
        self.finished = True
        try:
            self.win.destroy()
        except Exception:
            pass
        self.on_done(action, self.item)

    def do_view(self):
        if self.finished or self.busy:
            return
        self.status.configure(text="正在打开微信…", fg=SKY)
        self._fade_out(lambda: self.finish("viewed"))

    def do_ignore(self):
        if self.finished or self.busy:
            return
        self.status.configure(text="已忽略", fg=TXT_3)
        self._fade_out(lambda: self.finish("ignored"))

    def do_reply(self):
        """生成 + 发送放后台线程；整条流程有硬超时。"""
        if self.finished or self.busy:
            return
        self.busy = True
        self.remaining = 99999
        self.countdown.configure(text="…", fg=CLAY)
        self.bar.set_ratio(1.0, CLAY)
        self.status.configure(text="按你的风格生成中…", fg=CLAY)
        self.btn_reply.set_enabled(False)
        self.btn_view.set_enabled(False)
        self.btn_reply.set_text("生成中…")

        def guard():
            time.sleep(REPLY_TIMEOUT)
            if not self.work_done:
                self._ui(lambda: self.status.configure(
                    text=f"超时（>{REPLY_TIMEOUT}s），已放弃", fg=ROSE))
                time.sleep(1.0)
                self._ui(lambda: self._fade_out(lambda: self.finish("ignored")))

        def work():
            try:
                import wcauto
                import wcdraft
                wxid, name = self.item["wxid"], self.item["name"]
                ctx = self.item.get("ctx") or []
                r = wcdraft.generate(name, ctx, wxid)
                cands = r.get("candidates") or []
                if not cands:
                    return self._fail("生成失败：" + (r.get("note") or ""))
                reply = cands[0]
                self._ui(lambda: (self.status.configure(
                    text=f"发送中：{reply}", fg=AMBER),
                    self.btn_reply.set_text("发送中…")))
                cfg = wcauto.load_cfg()
                res = wcauto.Sender(cfg).send(name, reply)
                if not res.get("ok"):
                    return self._fail("发送失败：" + str(res.get("error")))
                if not res.get("dry_run"):
                    wcauto.Policy(cfg).mark_sent(wxid, self.item.get("fp") or "")
                head = "演练完成（未真发）：" if res.get("dry_run") else "已发送："
                self.work_done = True
                self._ui(lambda: (self.status.configure(text=head + reply, fg=CLAY_D),
                                  self.btn_reply.set_text("已回复")))
                time.sleep(1.0)
                self._ui(lambda: self._fade_out(lambda: self.finish("replied")))
            except Exception as e:
                self._fail(f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        threading.Thread(target=guard, daemon=True).start()

    def _fail(self, msg: str):
        self.work_done = True
        self._ui(lambda: (self.status.configure(text=msg[:110], fg=ROSE),
                          self.btn_reply.set_text("重试"),
                          self.btn_reply.set_enabled(True),
                          self.btn_view.set_enabled(True)))
        time.sleep(2.0)
        self._ui(lambda: self._fade_out(lambda: self.finish("ignored")))

    def _ui(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass


# ==========================================================================
# 自测
# ==========================================================================
def _selftest():
    import json as _json
    events = []
    fails = []

    m = AlertManager(on_view=lambda it: events.append(["view", it["name"]]),
                     on_reply=lambda it: events.append(["reply", it["name"]]),
                     on_ignore=lambda it, w: events.append([w, it["name"]]),
                     timeout=DEFAULT_TIMEOUT)

    # 把 _fail 的原因也记下来，否则只能看到"变成了 ignored"这种结果
    _orig_fail = AlertWindow._fail

    def _logged_fail(self, msg):
        fails.append(msg)
        return _orig_fail(self, msg)

    AlertWindow._fail = _logged_fail

    def ctx_for(name):
        return [{"dir": "in", "sender": name, "text": "明天下午有空吗？一起去打球"},
                {"dir": "out", "text": "我看下时间"},
                {"dir": "in", "sender": name, "text": "怎么样"}]

    m.push("a", "小王", "明天下午有空吗？一起去打球", fp="t1", ctx=ctx_for("小王"))
    m.push("b", "小李", "在吗", fp="t2", ctx=ctx_for("小李"))
    m.push("c", "羽毛球群", "今晚还有人吗", is_group=True, fp="t3", ctx=ctx_for("群"))
    dup = m.push("a", "小王", "重复的应该被拒", fp="t1")

    def auto():
        time.sleep(3.0)
        if m.current:
            m.current.do_reply()
        t0 = time.time()
        while m.current is not None and time.time() - t0 < 35:
            time.sleep(0.3)
        if m.current:
            m.current.do_view()
        time.sleep(2.5)
        time.sleep(12)
        m.stop()

    threading.Thread(target=auto, daemon=True).start()
    m.run()

    ok = (["reply", "小王"] in events and ["view", "小李"] in events
          and ["timeout", "羽毛球群"] in events)
    result = {"events": events, "stats": m.stats, "fails": fails,
              "dup_rejected": dup is False, "queue_len": 3, "pass": ok}
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_alert_selftest.json")
    with open(out, "w", encoding="utf-8") as f:
        _json.dump(result, f, ensure_ascii=False, indent=1)
    print("结果已写入", out)
    print("pass =", ok)


if __name__ == "__main__":
    _selftest()
