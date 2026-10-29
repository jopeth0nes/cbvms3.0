"""Native sidebar geometry checks; no database, camera, or dashboard workers."""
import unittest
from unittest.mock import Mock

import customtkinter as ctk

from ui.dashboard import CBVMSDashboard


class DashboardSidebarTests(unittest.TestCase):
    def test_small_windows_keep_logout_visible_and_navigation_reachable(self):
        for superadmin in (False, True):
            with self.subTest(superadmin=superadmin):
                app = ctk.CTk()
                try:
                    app.geometry('1280x540')
                    app.grid_rowconfigure(1, weight=1)
                    app._is_superadmin = superadmin
                    app.username = 'Layout test'
                    app._active_nav = 'live'
                    app._open_alerts_from_bell = Mock()
                    app._on_nav_select = Mock()
                    app._logout = Mock()
                    CBVMSDashboard._build_left_sidebar(app)
                    app.update()

                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)

                    widgets = list(descendants(app))
                    logout = next(w for w in widgets if isinstance(w, ctk.CTkButton)
                                  and w.cget('text') == 'Logout')
                    self.assertTrue(logout.winfo_ismapped(), 'Logout is clipped by navigation')
                    self.assertLessEqual(logout.winfo_rooty() + logout.winfo_height(),
                                         app.winfo_rooty() + app.winfo_height())
                    scroll = next(w for w in widgets if isinstance(w, ctk.CTkScrollableFrame))
                    canvas = scroll._parent_canvas
                    self.assertLess(canvas.yview()[1], 1, 'Menu should overflow on a short screen')
                    app._nav_buttons['live'].event_generate('<MouseWheel>', delta=-120)
                    app.update()
                    self.assertGreater(canvas.yview()[0], 0, 'Wheel over a menu button must scroll')
                    canvas.yview_moveto(1)
                    app.update()
                    settings = app._nav_buttons['settings']
                    self.assertGreaterEqual(settings.winfo_rooty(), canvas.winfo_rooty())
                    self.assertLessEqual(settings.winfo_rooty() + settings.winfo_height(),
                                         canvas.winfo_rooty() + canvas.winfo_height())
                    settings.invoke()
                    app._on_nav_select.assert_called_once_with('settings')
                    self.assertEqual('accounts' in app._nav_buttons, superadmin)
                    self.assertEqual('training' in app._nav_buttons, superadmin)
                    self.assertEqual('recycle_bin' in app._nav_buttons, superadmin)
                    # Use the dashboard's real geometry method for every reachable
                    # destination in each role; no camera or business data involved.
                    app._activity_sidebar = ctk.CTkFrame(app, width=280)
                    app._activity_sidebar.grid(row=1, column=2, sticky='nsew')
                    app._title_row = ctk.CTkFrame(app)
                    app._title_row.grid(row=0, column=1)
                    app._center_panel = ctk.CTkFrame(app)
                    app._center_panel.grid(row=1, column=1, sticky='nsew')
                    app.grid_columnconfigure(1, weight=1)
                    for _ in range(2):
                        for key in (*app._nav_buttons, 'alerts'):
                            app._active_nav = key
                            CBVMSDashboard._sightings_layout(app, key in {'unknown', 'attendance', 'records'})
                            app.update_idletasks()
                            self.assertEqual(bool(app._activity_sidebar.winfo_ismapped()), key == 'live', key)
                            self.assertEqual(app._center_panel.grid_info()['columnspan'], 1 if key == 'live' else 2)
                            self.assertEqual(app.grid_columnconfigure(2)['minsize'], 280 if key == 'live' else 0)
                    for height in (600, 768, 900, 540):
                        app.geometry(f'1280x{height}')
                        app.update()
                        self.assertTrue(logout.winfo_ismapped())
                        self.assertLessEqual(logout.winfo_rooty() + logout.winfo_height(),
                                             app.winfo_rooty() + app.winfo_height())
                    logout.invoke()
                    app._logout.assert_called_once_with()
                finally:
                    app.destroy()


if __name__ == '__main__':
    unittest.main()
