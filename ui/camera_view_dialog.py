"""Non-modal presentation chooser. Tuple fonts avoid worker-GC Tk finalizers."""
import tkinter as tk
import customtkinter as ctk
from ui.components import (COLOR_BG, COLOR_SURFACE, COLOR_BORDER, COLOR_ACCENT,
                           COLOR_TEXT, COLOR_TEXT_MUTED, COLOR_WARNING)


def validated_selection(preference, available, fallback):
    """Remember only stable keys; stale/malformed preferences cannot select URLs."""
    preference = preference if isinstance(preference, dict) else {}
    keys = []
    for value in preference.get('sources', []) if isinstance(preference.get('sources'), list) else []:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            key = tuple(value)
            if isinstance(key[0], str) and isinstance(key[1], (str, int)) and key in available and key not in keys:
                keys.append(key)
    if not keys and fallback in available:
        keys = [fallback]
    destination = preference.get('destination', 'Live Monitor')
    if destination not in ('Live Monitor', 'Separate window'):
        destination = 'Live Monitor'
    return keys[:2], destination


class CameraViewDialog(ctk.CTkToplevel):
    def __init__(self, master, sources, selected, destination, status, accept, closed):
        super().__init__(master, fg_color=COLOR_BG)
        self.title('Open camera view')
        self.geometry('500x510')
        self.minsize(460, 480)
        self.transient(master.winfo_toplevel())
        self._accept, self._closed_callback = accept, closed
        self._vars, self._cards = {}, {}
        self._available = set(sources)
        self.destination = f'{self}_destination'
        self.setvar(self.destination, destination)
        body = tk.Frame(self, bg=COLOR_BG, padx=24, pady=20)
        body.pack(fill='both', expand=True)
        def label(text, size=13, color=COLOR_TEXT):
            item = tk.Label(body, text=text, bg=COLOR_BG, fg=color,
                            font=('Helvetica', size), anchor='w')
            item.pack(fill='x', pady=(0, 8))
            return item
        label('Open camera view', 22)
        label('Choose what to display. Analysis source stays unchanged.', 11, COLOR_TEXT_MUTED)
        self._statuses = {}
        for key, source in sources.items():
            var = self._vars[key] = f'{self}_camera_{len(self._vars)}'
            self.setvar(var, int(key in selected))
            card = tk.Frame(body, bg=COLOR_SURFACE, highlightthickness=2)
            card.pack(fill='x', pady=4)
            self._cards[key] = card
            check = tk.Checkbutton(card, text=source.get('label', 'Camera'), variable=var,
                command=self.refresh, bg=COLOR_SURFACE, fg=COLOR_TEXT,
                activebackground=COLOR_SURFACE, activeforeground=COLOR_TEXT,
                selectcolor=COLOR_ACCENT, font=('Helvetica', 14), anchor='w',
                highlightcolor=COLOR_WARNING, highlightthickness=2, padx=10, pady=6)
            check.pack(fill='x')
            text = tk.Label(card, text=status(key), bg=COLOR_SURFACE, fg=COLOR_TEXT_MUTED,
                            font=('Helvetica', 11), anchor='w', padx=14)
            text.pack(fill='x', pady=(0, 6))
            self._statuses[key] = text
        label('Destination', 13)
        row = tk.Frame(body, bg=COLOR_BG)
        row.pack(fill='x')
        for name in ('Live Monitor', 'Separate window'):
            tk.Radiobutton(row, text=name, value=name, variable=self.destination,
                bg=COLOR_BG, fg=COLOR_TEXT, selectcolor=COLOR_ACCENT,
                activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
                highlightcolor=COLOR_WARNING, font=('Helvetica', 12)).pack(side='left', padx=(0, 12))
        self.preview = label('', 16, COLOR_WARNING)
        self.explanation = label('', 11, COLOR_TEXT_MUTED)
        actions = tk.Frame(body, bg=COLOR_BG)
        actions.pack(side='bottom', fill='x', pady=(12, 0))
        tk.Button(actions, text='Cancel', command=self.cancel, font=('Helvetica', 13),
                  highlightcolor=COLOR_WARNING).pack(side='left')
        self.open_button = tk.Button(actions, text='Open view', command=self.submit,
            font=('Helvetica', 13, 'bold'), highlightcolor=COLOR_WARNING, default='active')
        self.open_button.pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda _: self.cancel())
        self.bind('<Return>', lambda _: self.submit())
        self.bind('<Destroy>', self._dispose_variables, add='+')
        self.refresh()

    def _dispose_variables(self, event):
        if event.widget is self:
            for var in [*self._vars.values(), self.destination]:
                try:
                    self.tk.globalunsetvar(var)
                except tk.TclError:
                    pass
            self._closed_callback(self)

    def selection(self):
        return [key for key, var in self._vars.items() if self.getvar(var)]

    def refresh(self):
        keys = self.selection()
        for key, card in self._cards.items():
            card.configure(highlightbackground=COLOR_ACCENT if key in keys else COLOR_BORDER)
        valid = 1 <= len(keys) <= 2 and all(k in self._available for k in keys)
        self.open_button.configure(state='normal' if valid else 'disabled')
        self.preview.configure(text='▣  Focus' if len(keys) == 1 else '▣  ▣  Side-by-side' if len(keys) == 2 else 'Select one or two cameras')
        missing = any(k not in self._available for k in keys)
        self.explanation.configure(text='A selected source was removed. Choose another camera.' if missing else
            'Layout follows your selection.' if valid else 'Choose one or two configured cameras to continue.')

    def update_status(self, status, available=None):
        if available is not None and set(available) != self._available:
            self._available = set(available)
            self.refresh()
        for key, widget in self._statuses.items():
            text = status(key)
            if widget.cget('text') != text:
                widget.configure(text=text)

    def submit(self):
        keys = self.selection()
        if 1 <= len(keys) <= 2 and all(k in self._available for k in keys):
            if self._accept(keys, self.getvar(self.destination)):
                self.cancel()
            else:
                self.explanation.configure(text='Use the analysis camera and one shared preview source.', wraplength=430)

    def cancel(self):
        self._closed_callback(self)
        if self.winfo_exists():
            self.destroy()
