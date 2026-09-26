"""Non-blocking college seal intro, followed by the login form."""
from pathlib import Path
import math
import time
import tkinter as tk

from PIL import Image, ImageTk


LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "yanga.png"


class LoginIntro:
    REVEAL_START = 1.8
    DURATION = 2.0

    def __init__(self, window, on_complete):
        self.window = window
        self.on_complete = on_complete
        self.job = None
        self.finished = False
        self.started = None
        self._photo_size = None
        self.photo = None
        self.logo = None
        try:
            with Image.open(LOGO_PATH) as source:
                self.logo = source.convert("RGBA")
        except (OSError, ValueError):
            pass  # An unavailable brand asset must never prevent signing in.
        self.canvas = tk.Canvas(window, bg="#11172D", highlightthickness=0, bd=0)
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.skip_binding = window.bind("<Escape>", lambda event: self.finish(), add="+")
        self.canvas.bind("<Button-1>", self._click)
        self.job = window.after(20, self._start)

    def _start(self):
        if self.finished or self.started is not None:
            return
        self.job = None
        # Only the intro exists during playback: no login widgets can rise
        # above it during CustomTkinter's deferred layout callbacks.
        tk.Misc.lift(self.canvas)
        self.window.update_idletasks()
        self.started = time.monotonic()
        self._tick()
        self.window.deiconify()

    @staticmethod
    def ease(value):
        value = max(0.0, min(1.0, value))
        # Zero velocity and acceleration at both ends of the movement.
        return value ** 3 * (value * (value * 6 - 15) + 10)

    def _click(self, event):
        if event.y > self.canvas.winfo_height() - 65:
            self.finish()

    def _tick(self):
        self.job = None
        if self.finished:
            return
        frame_started = time.monotonic()
        elapsed = frame_started - self.started
        if elapsed >= self.DURATION:
            self.finish()
            return
        canvas = self.canvas
        w, h = canvas.winfo_width(), canvas.winfo_height()
        cx, cy = w / 2, h * .40
        scale = min(w / 920, h / 620)
        canvas.delete("all")
        gold = "#D0AE68"
        reveal = self.ease(elapsed / 1.25)
        pulse = math.sin(elapsed * 2.8) * 3 * reveal
        cy += math.sin(elapsed * 2) * 5 * scale
        radius = (138 + 35 * (1 - reveal) + pulse) * scale
        # Deterministic drifting lights add depth without flashing or randomness.
        for i in range(28):
            x = ((i * 137.5 + elapsed * (7 + i % 4)) % 920) * w / 920
            y = ((i * 83.7 - elapsed * (10 + i % 6)) % 620) * h / 620
            dot = (1 + i % 3 * .5) * scale
            canvas.create_oval(x-dot, y-dot, x+dot, y+dot,
                               fill=("#354056", "#6E654C", "#47516C")[i % 3], outline="")
        # Layered halo expands gently behind the original, unmodified seal.
        for extra, color in ((31, "#171E35"), (20, "#1D253E"), (9, "#252E48")):
            r = radius + extra * scale
            canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=color, outline="")
        for extra in (16, 39, 66):
            r = radius + extra * scale
            canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline="#29314A", width=1)
        for offset, extent in ((0, 74), (120, 38), (240, 54)):
            r = radius + 16 * scale
            canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=offset-elapsed*58,
                              extent=extent * reveal, style="arc", outline=gold, width=2)
        r = radius + 39 * scale
        for offset in range(0, 360, 60):
            canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=offset+elapsed*28,
                              extent=22*reveal, style="arc", outline="#7180AA", width=1)
        for i in range(3):
            angle = elapsed * .8 + i * math.tau / 3
            for trail in range(7):
                a = angle - trail * .045
                x, y = cx + math.cos(a)*r, cy + math.sin(a)*r
                dot = max(.6, 2.6-trail*.3)*scale
                canvas.create_oval(x-dot, y-dot, x+dot, y+dot,
                                   fill=gold if trail < 2 else "#665B43", outline="")
        angle = elapsed * .8
        r = radius + 39 * scale
        x, y = cx + math.cos(angle)*r, cy + math.sin(angle)*r
        canvas.create_oval(x-3, y-3, x+3, y+3, fill=gold, outline="")
        if self.logo is not None:
            size = max(1, int((190 + 66 * reveal) * scale))
            if size != self._photo_size:
                frame = self.logo.resize((size, size), Image.Resampling.LANCZOS)
                self._logo_frame = frame
                self.photo = ImageTk.PhotoImage(frame, master=self.window)
                self._photo_size = size
            canvas.create_image(cx, cy, image=self.photo)
        else:
            canvas.create_text(cx, cy, text="CBVMS", fill=gold,
                               font=("Segoe UI", int(38*scale), "bold"))
        title_y = h * .76 + 12 * (1 - reveal)
        canvas.create_text(cx, title_y, text="DR. YANGA'S COLLEGES, INC.",
                           fill="#F1E3C1", font=("Segoe UI", max(12, int(19*scale)), "bold"))
        canvas.create_text(cx, title_y+32*scale, text="CAMPUS ENTRANCE MONITORING",
                           fill="#B0B8CD", font=("Segoe UI", max(10, int(11*scale))))
        bar_y = title_y + 64*scale
        canvas.create_line(cx-70*scale, bar_y, cx+70*scale, bar_y, fill="#30374C", width=2)
        canvas.create_line(cx-70*scale, bar_y, cx-70*scale+140*scale*min(elapsed/self.REVEAL_START, 1),
                           bar_y, fill=gold, width=2)
        canvas.create_text(cx, h-25, text="Skip intro  /  Esc", fill="#B0B8CD",
                           font=("Segoe UI", 10))
        # Account for render time instead of adding it to every frame interval.
        delay = max(1, round(1000/60 - (time.monotonic()-frame_started)*1000))
        self.job = self.window.after(delay, self._tick)

    def _prepare_login(self):
        if self.on_complete is not None:
            callback, self.on_complete = self.on_complete, None
            callback()

    def finish(self):
        if self.finished:
            return
        self._prepare_login()
        tk.Misc.lift(self.canvas)
        self.window.update_idletasks()
        self.cancel()
        self.window.deiconify()
        self.window.username_entry.focus_set()
        callback = getattr(self.window, "_on_ready", None)
        if callback is not None:
            self.window._on_ready = None
            callback()

    def cancel(self):
        self.finished = True
        if self.job is not None:
            self.window.after_cancel(self.job)
            self.job = None
        self.window.unbind("<Escape>", self.skip_binding)
        self.canvas.destroy()
        self.photo = None
