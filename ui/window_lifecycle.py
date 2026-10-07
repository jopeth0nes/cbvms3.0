"""Root-window lifecycle helpers for sequential CustomTkinter workspaces."""
import tkinter as tk
import customtkinter as ctk


class WorkspaceWindow(ctk.CTk):
    def destroy(self):
        if getattr(self, "_destroying", False):
            return
        self._destroying = True
        # A root owns its Tcl interpreter. Cancel even widget/library callbacks
        # before destroying it so they cannot fire against the next login root.
        try:
            for job in self.tk.splitlist(self.tk.call("after", "info")):
                # Cancel scheduling only. Each child must delete its own Tcl
                # command during destruction; root.after_cancel would delete
                # it early and make child.destroy fail with TclError.
                self.tk.call("after", "cancel", job)
        except tk.TclError:
            pass
        try:
            super().destroy()
        finally:
            self.quit()

    def reveal_when_ready(self):
        """Map the workspace without recursively draining layout callbacks.

        Tk completes pending layout in its normal event loop. A synchronous
        idle drain can starve the handoff when configure handlers keep resizing.
        """
        self.deiconify()
        self.lift()
