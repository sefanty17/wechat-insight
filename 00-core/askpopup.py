# -*- coding: utf-8 -*-
"""
askpopup.py — 开发过程中的「弹个小窗问你」工具

设计约束（用户明确要求）：
  · **必须能关掉**。无边框窗口如果没有关闭途径，就会卡住用户，很烦。
    所以给了三条退路：✕ 按钮 / Esc 键 / 超时自动关。
  · 关掉时返回 None，调用方据此知道"用户没选"。
  · 无边框窗口要能拖动，否则挡住东西又挪不开。

用法（Python）：
    from askpopup import ask
    ans = ask("标题", "问题", ["选项A", "选项B"], timeout=0)
    if ans is None:
        ...  # 用户关掉了

命令行：
    python askpopup.py "标题" "问题" "选项1|选项2"
"""
from __future__ import annotations

import sys
import tkinter as tk

CARD = "#FFFBF5"
LINE = "#EADFD0"
TXT = "#3A322B"
TXT_2 = "#8A7A6A"
TXT_3 = "#B3A697"
CLAY = "#C96F4A"
CLAY_D = "#A85636"
SKY = "#5B8DB8"
SKY_D = "#457197"
STONE = "#EDE4D8"
STONE_D = "#DED2C2"
FONT = "Microsoft YaHei UI"


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class _Btn(tk.Canvas):
    def __init__(self, parent, text, color, hover, cmd, w=120, h=30, fs=9):
        super().__init__(parent, width=w, height=h, bg=CARD,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.cmd, self.color, self.hover = cmd, color, hover
        self.shape = _round_rect(self, 1, 1, w - 1, h - 1, 8, fill=color, outline="")
        self.txt = self.create_text(w / 2, h / 2, text=text, fill="#FFFFFF",
                                    font=(FONT, fs, "bold"))
        self.bind("<Enter>", lambda e: self.itemconfigure(self.shape, fill=hover))
        self.bind("<Leave>", lambda e: self.itemconfigure(self.shape, fill=color))
        self.bind("<ButtonRelease-1>", lambda e: cmd())


def ask(title: str, message: str, options: list, timeout: int = 0,
        default: int = 0):
    """弹窗提问。返回被选中的选项文本；**用户关掉窗口则返回 None**。

    timeout>0 时超时自动选 default 项（不返回 None）。
    """
    result = {"v": None, "closed": False}

    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=LINE)

    W = 470
    n_lines = max(2, min(10, len(message) // 32 + message.count("\n") + 1))
    H = 92 + n_lines * 17 + 48
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x0, y0 = (sw - W) // 2, (sh - H) // 3
    win.geometry(f"{W}x{H}+{x0}+{y0}")

    outer = tk.Frame(win, bg=LINE)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    body = tk.Frame(outer, bg=CARD)
    body.pack(fill="both", expand=True)

    # ---------- 标题栏（可拖动 + 有关闭按钮）----------
    bar = tk.Frame(body, bg=CARD)
    bar.pack(fill="x", padx=14, pady=(12, 4))
    title_lbl = tk.Label(bar, text=title, bg=CARD, fg=TXT,
                         font=(FONT, 11, "bold"), anchor="w")
    title_lbl.pack(side="left", fill="x", expand=True)

    def _quit():
        try:
            win.destroy()
            root.quit()
        except Exception:
            pass

    def close():
        result["closed"] = True
        _quit()

    x_btn = tk.Label(bar, text="✕", bg=CARD, fg=TXT_3, font=(FONT, 11),
                     padx=6, cursor="hand2")
    x_btn.pack(side="right")
    x_btn.bind("<Button-1>", lambda e: close())
    x_btn.bind("<Enter>", lambda e: x_btn.configure(fg=CLAY, bg="#F6EDE2"))
    x_btn.bind("<Leave>", lambda e: x_btn.configure(fg=TXT_3, bg=CARD))

    # 拖动：无边框窗口必须有这个，否则挡住东西挪不开
    drag = {"x": 0, "y": 0}

    def start_drag(e):
        drag["x"], drag["y"] = e.x_root - win.winfo_x(), e.y_root - win.winfo_y()

    def do_drag(e):
        win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

    for w_ in (bar, title_lbl):
        w_.bind("<Button-1>", start_drag)
        w_.bind("<B1-Motion>", do_drag)

    tk.Label(body, text=message, bg=CARD, fg=TXT_2, font=(FONT, 10),
             wraplength=W - 34, justify="left", anchor="w").pack(fill="x", padx=16)

    row = tk.Frame(body, bg=CARD)
    row.pack(fill="x", padx=14, pady=(14, 14), side="bottom")

    def pick(v):
        result["v"] = v
        _quit()

    for i, opt in enumerate(options[:4]):
        col = [CLAY, SKY, STONE, STONE][i]
        hov = [CLAY_D, SKY_D, STONE_D, STONE_D][i]
        _Btn(row, opt[:14], col, hov, lambda o=opt: pick(o),
             w=max(84, min(150, len(opt) * 13 + 28))).pack(side="left", padx=4)

    # 提示行：把"怎么关"写出来，用户不用猜
    hints = ["Esc 或右上角 ✕ 关闭"]
    if timeout > 0:
        hints.insert(0, f"{timeout}s 后自动选「{options[default]}」")
    tk.Label(body, text="  ·  ".join(hints), bg=CARD, fg=TXT_3,
             font=(FONT, 8)).pack(side="bottom", pady=(0, 3))

    # 键盘：Esc 关闭（返回 None），数字键选第 N 项
    win.bind("<Escape>", lambda e: close())
    for i in range(min(4, len(options))):
        win.bind(f"<Key-{i+1}>", lambda e, o=options[i]: pick(o))

    if timeout > 0:
        def _auto():
            if result["v"] is None and not result["closed"]:
                pick(options[default])
        win.after(timeout * 1000, _auto)

    win.protocol("WM_DELETE_WINDOW", close)
    win.lift()
    win.attributes("-topmost", True)
    win.focus_force()
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass
    return result["v"]


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        print(ask(sys.argv[1], sys.argv[2], sys.argv[3].split("|")))
    else:
        print(ask("自测 · 现在可以关掉了",
                  "试试点右上角 ✕ 或者按 Esc，应该立刻关掉并返回 None。\n"
                  "这个窗口也能拖动（按住标题栏）。",
                  ["选了第一项", "选了第二项"], timeout=0))
