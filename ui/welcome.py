"""Shared, non-modal welcome for authenticated campus workspaces."""
from pathlib import Path
import customtkinter as ctk
from PIL import Image


def welcome_banner(master, name: str, role: str):
    banner = ctk.CTkFrame(master, fg_color="#19343E", corner_radius=16,
                         border_width=1, border_color="#38545B")
    banner.grid_columnconfigure(1, weight=1)
    try:
        with Image.open(Path(__file__).resolve().parents[1] / "assets" / "yanga.png") as source:
            image = ctk.CTkImage(source.copy(), size=(48, 48))
        seal = ctk.CTkLabel(banner, text="", image=image)
        seal.image = image
        seal.grid(row=0, column=0, rowspan=2, padx=(20, 16), pady=16)
    except OSError:
        pass
    ctk.CTkLabel(banner, text=f"Welcome, {name}.", anchor="w",
                 font=ctk.CTkFont(family="Segoe UI", size=23, weight="bold"),
                 text_color="#F5F2E9").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(banner, text="Your campus workspace is ready.", anchor="w",
                 font=ctk.CTkFont(family="Segoe UI", size=12),
                 text_color="#B8CDD0").grid(row=1, column=1, sticky="ew", pady=(0, 16))
    ctk.CTkLabel(banner, text=role.upper(), corner_radius=8,
                 fg_color="#294951", text_color="#E4C78C", padx=12,
                 font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold")
                 ).grid(row=0, column=2, rowspan=2, padx=20)
    return banner
