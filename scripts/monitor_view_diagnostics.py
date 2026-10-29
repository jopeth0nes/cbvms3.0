"""Accepted-view workflow in one real dashboard, driven through actual controls."""
import time
import tkinter as tk
from core.preview_session import source_key


def start_views(root, ws, stats, record, snapshot, finish, phase):
    names = root._cam_sources
    usb_name = next(n for n,s in names.items() if s['type']=='usb')
    ip_name = next(n for n,s in names.items() if s['type'] in ('ip','rj45'))
    usb, ip = source_key(names[usb_name]), source_key(names[ip_name])
    def choose(name):
        root._cam_switcher.open()
        root._cam_switcher.select(name)
    def ready(name):
        return root._camera_state=='STREAMING' and source_key(root._camera_requested_source)==source_key(names[name])
    def configure(keys, destination):
        ws.open_view_dialog()
        dialog=ws._dialog
        for key,var in dialog._vars.items():
            if dialog.tk.getboolean(dialog.getvar(var)) != (key in keys):
                control=next(w for w in dialog._cards[key].winfo_children() if isinstance(w,tk.Checkbutton))
                control.invoke()
        assert set(dialog.selection()) == set(keys), 'Checkbox selection disagrees with requested IDs'
        assert ('Side-by-side' if len(keys)==2 else 'Focus') in dialog.preview.cget('text')
        dialog.setvar(dialog.destination,destination)
        dialog.refresh()
        dialog.open_button.invoke()
        if destination == 'Separate window':
            assert set(ws._popup_keys) == set(keys)
            primary=source_key(root._camera_requested_source)
            assert bool(ws._popup_primary_tile.winfo_manager()) == (primary in keys)
            assert bool(ws._popup_secondary_tile.winfo_manager()) == any(k != primary for k in keys)
    def popup_frames(both=False):
        feeds=[ws._popup_feed,ws._popup_primary_feed] if both else [ws._popup_feed]
        return ws._popup is not None and all(f and f._photo is not None for f in feeds)
    def close(): root.tk.call(ws._popup.protocol('WM_DELETE_WINDOW'))
    steps=[('usb_primary',lambda:choose(usb_name),lambda:ready(usb_name)),
           ('dialog_cancel',lambda:(ws.open_view_dialog(),ws._dialog.cancel()),lambda:ws._dialog is None),
           ('usb_separate',lambda:configure([usb],'Separate window'),lambda:ws._popup_primary_feed is not None and ws._popup_primary_feed._photo is not None),
           ('close_single',close,lambda:ws._popup is None),
           ('main_other',lambda:configure([ip],'Live Monitor'),lambda:ws._last.get('tile') is not None),
           ('main_both',lambda:configure([usb,ip],'Live Monitor'),lambda:ws.tabs.get()=='Split'),
           ('both_separate',lambda:configure([usb,ip],'Separate window'),lambda:popup_frames(True)),
           ('gate_only_reconfigure',lambda:configure([ip],'Separate window'),popup_frames),
           ('usb_only_reconfigure',lambda:configure([usb],'Separate window'),lambda:ws._popup_primary_feed._photo is not None),
           ('both_again',lambda:configure([usb,ip],'Separate window'),lambda:popup_frames(True)),
           ('resize',lambda:ws._popup.geometry('1050x650'),lambda:True),
           ('minimize',lambda:ws._popup.iconify(),lambda:True),
           ('restore',lambda:ws._popup.deiconify(),lambda:True),
           ('fullscreen',lambda:ws._popup.attributes('-fullscreen',True),lambda:True),
           ('exit_fullscreen',lambda:ws._popup.attributes('-fullscreen',False),lambda:True),
           ('close_both',close,lambda:ws._popup is None),
           ('ip_primary',lambda:choose(ip_name),lambda:ready(ip_name)),
           ('usb_companion',lambda:configure([usb],'Separate window'),popup_frames),
           ('close_usb_companion',close,lambda:ws._popup is None),
           ('both_ip_primary',lambda:configure([usb,ip],'Separate window'),lambda:popup_frames(True)),
           ('reconfigure_ip_only',lambda:configure([ip],'Separate window'),lambda:ws._popup_primary_feed.winfo_ismapped()),
           ('reconfigure_both',lambda:configure([usb,ip],'Separate window'),lambda:popup_frames(True)),
           ('settings',lambda:root._on_nav_select('settings'),lambda:ws._popup is None),
           ('return_live',lambda:root._on_nav_select('live'),lambda:ready(ip_name)),
           ('usb_back',lambda:choose(usb_name),lambda:ready(usb_name)),
           ('final_both',lambda:configure([usb,ip],'Separate window'),lambda:popup_frames(True)),
           ('final_close',close,lambda:ws._popup is None)]
    def step(index):
        if index==len(steps):
            stats['view_steps_completed']=index
            snapshot('view_workflow_complete',index)
            finish()
            return
        name,action,predicate=steps[index]
        phase[0]=name
        record('view_action',name=name)
        action()
        start=time.monotonic()
        def poll():
            if predicate():
                snapshot(name,index)
                stats['view_steps_completed']=index+1
                root.after(500,lambda:step(index+1))
            elif time.monotonic()-start>22:
                stats['errors'].append(name+'Timeout')
                finish()
            else: root.after(40,poll)
        root.after(60,poll)
    step(0)
