"""Shared dependent academic controls; all catalog operations are in memory."""
import customtkinter as ctk
from core.academics import (COLLEGES, YEAR_LEVELS, SELECT_COLLEGE, SELECT_COURSE,
    SELECT_YEAR, courses_for, academic_values, display_academics, NEEDS_REVIEW)


class AcademicFields(ctk.CTkFrame):
    def __init__(self, parent, student=None):
        super().__init__(parent, fg_color='transparent')
        self.columnconfigure(1, weight=1)
        self.college = ctk.CTkOptionMenu(self, values=[SELECT_COLLEGE, *COLLEGES], command=self.change_college, dynamic_resizing=False)
        self.course = ctk.CTkOptionMenu(self, values=[SELECT_COURSE], state='disabled', dynamic_resizing=False,
                                        command=lambda value: self.selection.configure(text=value))
        # Editable year accepts any positive number: these suggestions are not program durations.
        self.year = ctk.CTkComboBox(self, values=list(YEAR_LEVELS))
        self.year.set(SELECT_YEAR)
        self.section = ctk.CTkEntry(self, placeholder_text='Section (optional)')
        for row, (label, widget) in enumerate((('College', self.college), ('Course', self.course),
                                               ('Year Level', self.year), ('Section', self.section))):
            ctk.CTkLabel(self, text=label, anchor='w').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=9)
            widget.grid(row=row, column=1, sticky='ew', pady=9)
        self.selection = ctk.CTkLabel(self, text='', anchor='w', justify='left', wraplength=450)
        self.selection.grid(row=4, column=0, columnspan=2, sticky='ew')
        self.bind('<Configure>', lambda event: self.selection.configure(wraplength=max(150, event.width - 20)))
        if student:
            values = display_academics(student)
            if values['college_department'] != NEEDS_REVIEW:
                self.college.set(values['college_department'])
                self.change_college(self.college.get())
                self.course.set(values['course'])
                self.selection.configure(text=values['course'])
            else:
                ctk.CTkLabel(self, text=f"{NEEDS_REVIEW}\nLegacy: {student.get('college_department') or '—'} / {student.get('course') or '—'}",
                    wraplength=500, anchor='w', justify='left').grid(row=5, column=0, columnspan=2, sticky='w')
            self.year.set(values['report_year_level'] or SELECT_YEAR)
            self.section.insert(0, values['report_section'])
        self.initial = self.raw()

    def change_college(self, college):
        courses = courses_for(college)
        self.course.configure(values=[SELECT_COURSE, *courses], state='normal' if courses else 'disabled')
        if self.course.get() not in courses:
            self.course.set(SELECT_COURSE)
            self.selection.configure(text="")

    def raw(self):
        return self.college.get(), self.course.get(), self.year.get(), self.section.get()

    def values(self):
        return academic_values(*self.raw())

    def changes(self):
        return None if self.raw() == self.initial else self.values()
