"""Stage 1 uniform dataset review. All disk/decode/detector work uses BackgroundTask."""
from pathlib import Path
import hashlib
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageTk

from core.uniform_dataset import UniformDataset, CATEGORIES
from core.trainer import ROOT, TRAIN_DIR
from ui.background_task import BackgroundTask
from ui.scrolling import ScrollableFrame
from ui.components import COLOR_BG, COLOR_SURFACE, COLOR_ACCENT, COLOR_TEXT_MUTED, heading_font

GARMENTS = ('campus_uniform', 'organizational_shirt', 'other_clothing')
PAGE_SIZE = 12


class UniformWorkspace(ctk.CTkFrame):
    def __init__(self, master, *, capture, dataset=None):
        super().__init__(master, fg_color=COLOR_BG)
        self.dataset = dataset or UniformDataset()
        self.capture = capture
        self.task = BackgroundTask(self)
        self.page = 0
        self._images = []
        self._review = None
        self._refresh_again = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.status = ctk.CTkLabel(self, text='Loading review dataset…', anchor='w', wraplength=950)
        self.status.grid(row=0, column=0, sticky='ew', padx=12, pady=6)
        self.tabs = ctk.CTkTabview(self, command=self._tab_changed)
        self.tabs.grid(row=1, column=0, sticky='nsew')
        self.views = {}
        for category in (*GARMENTS, 'unassigned'):
            title = CATEGORIES[category] if category in GARMENTS else 'Evaluation & Models'
            tab = self.tabs.add(title)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(3, weight=1)
            if category == 'unassigned':
                self.readiness = ctk.CTkLabel(tab, text='Reading readiness…', justify='left', anchor='w', wraplength=900)
                self.readiness.grid(row=0, column=0, sticky='ew', padx=8)
                actions = ctk.CTkFrame(tab, fg_color='transparent')
                actions.grid(row=1, column=0, sticky='ew')
                ctk.CTkButton(actions, text='Audit legacy labels', command=self.audit_legacy).pack(side='left', padx=4)
                for title in ('Train candidate', 'Evaluate candidate', 'Activate model'):
                    ctk.CTkButton(actions, text=title, state='disabled', width=140).pack(side='left', padx=4)
                text = 'Legacy review required • Correct/Wrong labels are not garment categories'
            else:
                ctk.CTkLabel(tab, text=CATEGORIES[category] + ' • review gallery', font=heading_font()).grid(row=0, column=0, sticky='w', padx=8)
                actions = ctk.CTkFrame(tab, fg_color='transparent')
                actions.grid(row=1, column=0, sticky='ew')
                for title, action in (
                    ('Upload Photos', lambda c=category: self.upload(c)),
                    ('Import Folder', lambda c=category: self.import_folder(c)),
                    ('Import ZIP', lambda c=category: self.import_zip(c)),
                    ('Capture from Camera', lambda c=category: self.capture(c))):
                    ctk.CTkButton(actions, text=title, command=action, width=145).pack(side='left', padx=4, pady=6)
                text = 'Each original is preserved. Imports need crop and label review before approval.'
            counts = ctk.CTkLabel(tab, text=text, anchor='w', wraplength=900)
            counts.grid(row=2, column=0, sticky='ew', padx=8, pady=4)
            gallery = ScrollableFrame(tab, fg_color=COLOR_BG)
            gallery.grid(row=3, column=0, sticky='nsew')
            for col in range(3):
                gallery.grid_columnconfigure(col, weight=1)
            footer = ctk.CTkFrame(tab, fg_color='transparent')
            footer.grid(row=4, column=0, sticky='ew')
            ctk.CTkButton(footer, text='Previous', width=95, command=lambda: self.move_page(-1)).pack(side='left', padx=4)
            page = ctk.CTkLabel(footer, text='')
            page.pack(side='left', padx=12)
            ctk.CTkButton(footer, text='Next', width=95, command=lambda: self.move_page(1)).pack(side='left', padx=4)
            self.views[category] = (gallery, counts, page)
        self.refresh()

    def category(self):
        title = self.tabs.get()
        return next((c for c in GARMENTS if CATEGORIES[c] == title), 'unassigned')

    def _tab_changed(self):
        self.page = 0
        self.refresh()

    def show_category(self, category):
        self.tabs.set(CATEGORIES[category])
        self._tab_changed()

    def _error(self, error):
        self.status.configure(text=str(error) + ' Any examples already saved remain available for review.')

    def run(self, operation, message, done=None, error=None):
        if self.task.busy:
            self.status.configure(text='An operation is still running. Please wait.')
            return False
        self.status.configure(text=message)
        def finished(value):
            self.status.configure(text='Saved to the review dataset. The active model is unchanged.')
            if done:
                done(value)
            self.refresh()
        def failed(message):
            self._error(message)
            if error:
                error(message)
        return self.task.run(operation, finished, failed)

    def refresh(self):
        if self.task.busy:
            self._refresh_again = True
            return
        category, page = self.category(), self.page
        dataset = self.dataset
        def load():
            snapshot = dataset.snapshot()
            records = [r for r in snapshot['records'] if r['category'] == category]
            max_page = max(0, (len(records) - 1) // PAGE_SIZE)
            actual = min(page, max_page)
            rows = []
            for record in records[actual * PAGE_SIZE:(actual + 1) * PAGE_SIZE]:
                image = dataset.image(record['image_id'])
                image.thumbnail((190, 125))
                rows.append((record, image))
            models = []
            for name in ('uniform_cls.pt', 'uniform_embed_ref.npz', 'uniform_color_ref.json'):
                path = ROOT / 'models' / name
                models.append(f'{name}: SHA256 {hashlib.sha256(path.read_bytes()).hexdigest()[:12]}' if path.is_file() else f'{name}: not present')
            return snapshot, records, rows, actual, models
        def loaded(value):
            snapshot, records, rows, actual, models = value
            if category != self.category() or page != self.page:
                self._refresh_again = False
                self.refresh()
                return
            self.page = actual
            gallery, counts, page_label = self.views[category]
            for widget in gallery.winfo_children():
                widget.destroy()
            self._images.clear()
            totals = {s: sum(r['status'] == s for r in records) for s in ('approved', 'pending_review', 'excluded')}
            counts.configure(text=f"{len(records)} unique images • {totals['approved']} approved • {totals['pending_review']} pending review • {totals['excluded']} excluded\n"
                + f"{sum(max(0, len(r.get('sources', []))-1) for r in records)} exact duplicate occurrences • {sum(bool(r.get('duplicate_of')) for r in records)} images flagged as near-duplicates")
            page_label.configure(text=f'Page {actual + 1} • showing {len(rows)} of {len(records)}')
            for i, (record, picture) in enumerate(rows):
                card = ctk.CTkFrame(gallery, fg_color=COLOR_SURFACE)
                card.grid(row=i//3, column=i%3, sticky='nsew', padx=5, pady=5)
                img = ctk.CTkImage(light_image=picture, dark_image=picture, size=picture.size)
                self._images.append(img)
                ctk.CTkLabel(card, text='', image=img).pack(pady=6)
                ctk.CTkLabel(card, text=f"{record['status'].replace('_', ' ')} • {record['width']} × {record['height']}\n{len(record.get('sources', []))} source occurrence(s)", wraplength=200).pack(padx=6)
                ctk.CTkButton(card, text='Review original & crop', command=lambda r=record: self.open_review(r)).pack(padx=6, pady=6)
            if not rows:
                ctk.CTkLabel(gallery, text='No examples yet. Import photos to begin review.', text_color=COLOR_TEXT_MUTED).grid(row=0, column=0, columnspan=3, pady=25)
            ready = snapshot.get('readiness', {})
            self.readiness.configure(text='Current model artifacts (unchanged): ' + ' • '.join(models) + '\n'
                + 'Experimental training: ' + str(ready.get('experimental_training_eligible', False)) +
                ' (minimum category coverage only) • Independent evaluation: ' + str(ready.get('independent_evaluation_ready', False)) + '\n'
                + 'Training and evaluation are future stages. Activation is disabled in Stage 1.\n'
                + 'Readiness: ' + '; '.join(ready.get('experimental_training_reasons', [])) + '\n'
                + 'Photo count alone does not establish accuracy. Unknown provenance stays grouped; no independent evaluation claims.\n'
                + f"Dataset: {snapshot.get('counts', {}).get('total', len(snapshot['records']))} unique originals. "
                + 'Review legacy examples below; confirm each garment category.')
            self.status.configure(text='Stage 1 • originals and review metadata only • active model unchanged')
            if self._refresh_again:
                self._refresh_again = False
                self.refresh()
        self.task.run(load, loaded, self._error)

    def move_page(self, delta):
        self.page = max(0, self.page + delta)
        self.refresh()

    def upload(self, category):
        dataset = self.dataset
        paths = filedialog.askopenfilenames(parent=self, title=f'Upload {CATEGORIES[category]} photos', filetypes=[('Images', '*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff')])
        if paths:
            self.run(lambda: dataset.import_paths(paths, category), 'Validating and importing photos…')

    def import_folder(self, category):
        dataset = self.dataset
        path = filedialog.askdirectory(parent=self, title='Import image folder')
        if path:
            self.run(lambda: dataset.import_folder(path, category), 'Validating folder and preserving originals…')

    def import_zip(self, category):
        dataset = self.dataset
        path = filedialog.askopenfilename(parent=self, title='Import image ZIP', filetypes=[('ZIP archive', '*.zip')])
        if path:
            self.run(lambda: dataset.import_zip(path, category), 'Validating ZIP and deduplicating images…')

    def audit_legacy(self):
        dataset = self.dataset
        self.run(lambda: dataset.audit_legacy(TRAIN_DIR / 'uniform'), 'Auditing legacy labels without mapping or changing originals…')

    def import_frames(self, frames, category, capture_group, done, error=None):
        dataset = self.dataset
        return self.run(lambda: [dataset.import_capture(f, category, capture_group) for f in frames],
                        'Saving captured originals for review…', done, error)

    def open_review(self, record):
        if self._review is not None and self._review.winfo_exists():
            self._review.lift()
            return
        self._review = ReviewWindow(self, self.dataset, record, self.refresh)


class ReviewWindow(ctk.CTkToplevel):
    def __init__(self, master, dataset, record, changed):
        super().__init__(master)
        self.title('Review uniform example')
        self.geometry('880x760')
        self.minsize(880, 500)
        self.transient(master.winfo_toplevel())
        self.dataset, self.record, self.changed = dataset, record, changed
        self.task = BackgroundTask(self)
        self._photo = None
        self._crop_photo = None
        self.scale = 1.0
        self._proposal_box = record.get('crop_proposal', {}).get('box')
        self.bind('<Escape>', lambda e: self.destroy())
        body = ScrollableFrame(self, fg_color=COLOR_BG)
        body.pack(fill='both', expand=True)
        self.message = ctk.CTkLabel(body, text='Loading original…', wraplength=780, justify='left')
        self.message.pack(fill='x', padx=10, pady=8)
        previews = ctk.CTkFrame(body)
        previews.pack(fill='x', padx=10)
        self.canvas = tk.Canvas(previews, width=390, height=300, bg='#101828', highlightthickness=0)
        self.canvas.pack(side='left', padx=4, pady=4)
        self.crop_preview = ctk.CTkLabel(previews, text='Proposed torso crop')
        self.crop_preview.pack(side='left', padx=8)
        self.canvas.bind('<ButtonPress-1>', self.start_crop)
        self.canvas.bind('<B1-Motion>', self.drag_crop)
        ctk.CTkLabel(body, text='Original raster • drag to correct the crop. Keep collar, sleeves, garment and emblem visible.\nNo mirror correction is applied automatically.', wraplength=780, justify='left').pack(padx=10, pady=6)
        self.category = ctk.CTkOptionMenu(body, values=list(CATEGORIES.values()))
        self.category.set(CATEGORIES[record['category']])
        self.category.pack(fill='x', padx=10, pady=4)
        coords = ctk.CTkFrame(body)
        coords.pack(fill='x', padx=10)
        self.coords = []
        for name, value in zip(('Left', 'Top', 'Right', 'Bottom'), record.get('crop') or self._proposal_box or [0, 0, record['width'], record['height']]):
            ctk.CTkLabel(coords, text=name).pack(side='left', padx=4)
            entry = ctk.CTkEntry(coords, width=82)
            entry.insert(0, str(value))
            entry.pack(side='left')
            self.coords.append(entry)
        buttons = ctk.CTkFrame(body, fg_color='transparent')
        buttons.pack(fill='x', padx=10, pady=4)
        ctk.CTkButton(buttons, text='Preview corrected crop', command=self.preview).pack(side='left', padx=4)
        ctk.CTkButton(buttons, text='Propose torso crop', command=self.propose).pack(side='left', padx=4)
        self.visibility = ctk.CTkOptionMenu(body, values=['unknown', 'visible', 'occluded', 'not_visible'])
        ctk.CTkLabel(body, text='Logo visibility').pack(anchor='w', padx=10)
        self.visibility.set(record.get('logo_visibility', 'unknown'))
        self.visibility.pack(fill='x', padx=10)
        ctk.CTkLabel(body, text='Optional logo box in original pixels: left, top, right, bottom (blank = none)').pack(anchor='w', padx=10)
        self.logo_box = ctk.CTkEntry(body)
        self.logo_box.pack(fill='x', padx=10)
        self.logo_box.insert(0, ', '.join(map(str, record.get('logo_box') or [])))
        self.flags = {}
        for flag, label in [('possible_blur', 'Possible blur'), ('obstruction', 'Garment obstructed'), ('low_resolution', 'Insufficient resolution'), ('incorrect_association', 'Wrong person / garment association'), ('provenance_association_uncertain', 'Association still needs review'), ('label_conflict', 'Conflicting labels still unresolved'), ('multiple_people', 'Multiple people: association unresolved'), ('person_association_uncertain', 'Detected person association needs review')]:
            check = ctk.CTkCheckBox(body, text=label)
            check.pack(anchor='w', padx=10, pady=3)
            if flag in record.get('flags', []):
                check.select()
            self.flags[flag] = check
        ctk.CTkLabel(body, text='Review notes / capture provenance (unknown stays grouped)').pack(anchor='w', padx=10)
        self.notes = ctk.CTkEntry(body)
        self.notes.insert(0, record.get('notes', ''))
        self.notes.pack(fill='x', padx=10, pady=4)
        self.confirm = ctk.CTkCheckBox(body, text='I checked the category, crop, association and quality warnings.')
        self.confirm.pack(anchor='w', padx=10, pady=8)
        actions = ctk.CTkFrame(body)
        actions.pack(fill='x', padx=10, pady=10)
        for title, status in [('Save pending review', 'pending_review'), ('Approve example', 'approved'), ('Exclude example', 'excluded')]:
            ctk.CTkButton(actions, text=title, command=lambda s=status: self.save(s)).pack(side='left', padx=4, pady=6)
        self.preview()

    def crop(self):
        box = [int(e.get()) for e in self.coords]
        w, h = self.record['width'], self.record['height']
        if not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            raise ValueError('Crop must be inside the original with positive width and height.')
        return box

    def set_crop(self, box):
        for entry, value in zip(self.coords, box):
            entry.delete(0, 'end')
            entry.insert(0, str(int(value)))
        self.draw_box()

    def draw_box(self):
        try:
            self.canvas.delete('crop')
            self.canvas.create_rectangle(*(v * self.scale for v in self.crop()), outline='#26c6b0', width=2, tags='crop')
        except ValueError:
            pass

    def start_crop(self, event):
        self.origin = (event.x, event.y)

    def drag_crop(self, event):
        x, y = getattr(self, 'origin', (event.x, event.y))
        w, h = self.record['width'], self.record['height']
        box = [max(0, min(w, round(min(x, event.x)/self.scale))), max(0, min(h, round(min(y, event.y)/self.scale))),
               max(0, min(w, round(max(x, event.x)/self.scale))), max(0, min(h, round(max(y, event.y)/self.scale)))]
        self.set_crop(box)

    def preview(self):
        try:
            box = self.crop()
        except ValueError as exc:
            self.message.configure(text=str(exc)); return
        dataset, image_id = self.dataset, self.record['image_id']
        def load():
            original = dataset.image(image_id)
            crop = original.crop(box)
            original.thumbnail((390, 300))
            crop.thumbnail((330, 300))
            return original, crop
        def loaded(result):
            original, crop = result
            self.scale = original.width / self.record['width']
            self._photo = ImageTk.PhotoImage(original, master=self.canvas)
            self.canvas.delete('all')
            self.canvas.create_image(0, 0, image=self._photo, anchor='nw')
            self.draw_box()
            self._crop_photo = ctk.CTkImage(light_image=crop, dark_image=crop, size=crop.size)
            self.crop_preview.configure(image=self._crop_photo, text='')
            self.message.configure(text=f"Original {self.record['image_id'][:12]} • {self.record['width']}×{self.record['height']}\n"
                + 'Flags: ' + ', '.join(self.record.get('flags', [])) + '\n'
                + 'Group: ' + str(self.record.get('capture_group') or 'unknown provenance')
                + f" • EXIF orientation: {self.record.get('exif_orientation', 1)} (raster preserved)\n"
                + 'Sources: ' + '; '.join(f"{Path(s['path']).name} [legacy: {s.get('legacy_label', 'none')}]" for s in self.record.get('sources', [])[:3]))
        self.task.run(load, loaded, lambda e: self.message.configure(text=e))

    def propose(self):
        dataset, image_id = self.dataset, self.record['image_id']
        def operation():
            box = dataset.propose_crop(image_id)
            record = next(r for r in dataset.snapshot()['records'] if r['image_id'] == image_id)
            return box, record
        def done(value):
            box, record = value
            self.record = record
            for flag, check in self.flags.items():
                if flag in record.get('flags', []):
                    check.select()
            if box:
                self._proposal_box = list(box)
                self.set_crop(box)
                self.confirm.deselect()
                self.preview()
            else:
                self.message.configure(text='No reliable torso proposal. Correct the crop manually.')
        self.task.run(operation, done, lambda e: self.message.configure(text=e))

    def save(self, status):
        try:
            if status == 'approved' and not self.confirm.get():
                raise ValueError('Confirm your review before approval.')
            box = self.crop()
            logo = [int(v.strip()) for v in self.logo_box.get().split(',')] if self.logo_box.get().strip() else None
            category = next(c for c, title in CATEGORIES.items() if title == self.category.get())
            flags = [f for f in self.record.get('flags', []) if f not in self.flags]
            flags += [f for f, check in self.flags.items() if check.get()]
            args = dict(category=category, crop=box, crop_source='detector' if box == self._proposal_box else 'manual', status=status, logo_visibility=self.visibility.get(), logo_box=logo, flags=flags, notes=self.notes.get())
        except (ValueError, StopIteration) as exc:
            self.message.configure(text=str(exc)); return
        dataset, image_id = self.dataset, self.record['image_id']
        def done(value):
            self.changed()
            self.destroy()
        self.task.run(lambda: dataset.review(image_id, **args), done, lambda e: self.message.configure(text=e))
