"""Compact keyboard-friendly multi-select checklist for report filters."""
import tkinter as tk
import customtkinter as ctk


def selection_after_toggle(values, selected, value, checked):
    chosen=set(values if selected is None else selected)
    chosen.add(value) if checked else chosen.discard(value)
    return [item for item in values if item in chosen]


def select_all(): return None
def clear_selection(): return []


class MultiSelectChecklist(ctk.CTkFrame):
    def __init__(self, master, title, values=(), *, on_change=None, on_removed=None, **kwargs):
        super().__init__(master, fg_color='transparent', **kwargs)
        self.title=title; self.values=list(values); self.selected=None; self.on_change=on_change; self.on_removed=on_removed
        self.button=ctk.CTkButton(self,text='All',anchor='w',command=self.open)
        self.button._canvas.configure(takefocus=True)
        self.button.pack(fill='x')
        self.button.bind('<space>',lambda _event:self.open())
        self.button.bind('<Return>',lambda _event:self.open())
    def get(self): return None if self.selected is None else list(self.selected)
    def set_enabled(self, enabled): self.button.configure(state='normal' if enabled else 'disabled')
    def set_options(self, values):
        values=list(dict.fromkeys(values)); old=self.selected; self.values=values
        if old is not None:
            self.selected=[v for v in old if v in values]
            removed=[v for v in old if v not in values]
            if removed and self.on_removed:self.on_removed(removed)
        self._summary()
    def set(self, values): self.selected=None if values is None else [v for v in values if v in self.values]; self._summary()
    def _summary(self):
        self.button.configure(text='All' if self.selected is None else ('None' if not self.selected else f'{len(self.selected)} selected'))
    def open(self):
        if self.button.cget('state') == 'disabled':return
        if getattr(self, '_popup', None) is not None and self._popup.winfo_exists():
            self._popup.lift();return
        win=ctk.CTkToplevel(self); win.title(self.title); win.transient(self.winfo_toplevel()); win.geometry('300x360')
        self._popup=win
        win.bind('<Escape>',lambda _e:win.destroy())
        search=tk.StringVar(); entry=ctk.CTkEntry(win,textvariable=search,placeholder_text='Search options'); entry.pack(fill='x',padx=10,pady=8)
        controls=ctk.CTkFrame(win,fg_color='transparent'); controls.pack(fill='x',padx=8)
        scroll=ctk.CTkScrollableFrame(win); scroll.pack(fill='both',expand=True,padx=8,pady=8)
        checks={}
        initial=set(self.values if self.selected is None else self.selected)
        all_mode=self.selected is None
        def toggle(value):
            nonlocal all_mode
            all_mode=False
            updated=selection_after_toggle(self.values,list(initial),value,checks[value].get())
            initial.clear();initial.update(updated)
        def draw(*_):
            for child in scroll.winfo_children(): child.destroy()
            query=search.get().casefold()
            for value in self.values:
                if query not in str(value).casefold(): continue
                var=checks.setdefault(value,tk.BooleanVar(value=value in initial));var.set(value in initial)
                checkbox=ctk.CTkCheckBox(scroll,text=str(value),variable=var,command=lambda v=value:toggle(v))
                checkbox._canvas.configure(takefocus=True)
                checkbox.pack(anchor='w',padx=5,pady=3)
                checkbox.bind('<space>',lambda _event,w=checkbox:w.toggle())
        search.trace_add('write',draw)
        def select_all():
            nonlocal all_mode
            all_mode=True
            initial.update(self.values)
            for var in checks.values(): var.set(True)
        def clear():
            nonlocal all_mode
            all_mode=False
            initial.clear()
            for var in checks.values(): var.set(False)
        def button(parent,text,command,**kwargs):
            widget=ctk.CTkButton(parent,text=text,command=command,**kwargs)
            widget._canvas.configure(takefocus=True)
            widget.bind('<space>',lambda _event:widget.invoke())
            return widget
        button(controls,'Select all',select_all,width=90).pack(side='left',padx=3)
        button(controls,'Clear',clear,width=70).pack(side='left',padx=3)
        def done():
            self.selected=None if all_mode else [v for v in self.values if v in initial]
            self._summary()
            if self.on_change:self.on_change()
            win.destroy()
        button(win,'Apply',done).pack(fill='x',padx=10,pady=(0,10))
        win.bind('<Return>',lambda _e:done())
        draw(); entry.focus_set()
