"""Supplied school catalog. Existing student columns are the authoritative labels.

Identifiers are stable application keys, never inferred from program durations.
Only the explicit aliases below may resolve legacy abbreviations.
"""
import re

CATALOG = {
    'ccs': ('College of Computer Studies', {
        'act': 'Associate in Computer Technology', 'ce': 'Computer Engineering',
        'cs': 'Computer Science', 'it': 'Information Technology'}),
    'coa': ('College of Accountancy', {
        'accountancy': 'Accountancy', 'ais': 'Accounting Information System'}),
    'cba': ('College of Business Administration', {
        'ba': 'Business Administration',
        'ba_finance': 'Business Administration Major in Financial Management',
        'ba_hr': 'Business Administration Major in Human Resource Development Management',
        'ba_marketing': 'Business Administration Major in Marketing Management',
        'ba_operations': 'Business Administration Major in Operations Management'}),
    'cht': ('College of Hospitality Management and Tourism', {
        'hospitality': 'Hospitality Management', 'tourism': 'Tourism Management'}),
    'cme': ('College of Maritime Education', {
        'marine_engineering': 'Marine Engineering', 'marine_transportation': 'Marine Transportation'}),
    'coe': ('College of Education', {
        'elementary': 'Elementary Education', 'secondary_english': 'Secondary Education Major in English',
        'secondary_filipino': 'Secondary Education Major in Filipino',
        'secondary_math': 'Secondary Education Major in Mathematics',
        'secondary_sciences': 'Secondary Education Major in Sciences',
        'cpte': 'Continuing Professional Teacher Education'}),
    'chs': ('College of Health and Sciences', {
        'midwifery': 'Midwifery', 'nursing': 'Nursing', 'caregiving': 'Caregiving NC II',
        'diploma_midwifery': 'Diploma in Midwifery'}),
}
COLLEGES = tuple(label for label, _ in CATALOG.values())
COURSES = {key: (college, label) for college, programs in CATALOG.values() for key, label in programs.items()}
ALIASES = {'BSIT': 'it', 'BSCS': 'cs', 'BSCPE': 'ce', 'ACT': 'act',
           'BSA': 'accountancy', 'BSAIS': 'ais', 'BSBA': 'ba', 'BSHM': 'hospitality',
           'BSTM': 'tourism', 'BSMT': 'marine_transportation',
           'BEED': 'elementary', 'BSN': 'nursing'}
NEEDS_REVIEW = 'Unspecified/Needs review'
SELECT_COLLEGE, SELECT_COURSE, SELECT_YEAR = 'Select college', 'Select course', 'Select year level'
NOT_APPLICABLE = 'Not applicable'


def courses_for(college):
    return tuple(label for owner, label in COURSES.values() if owner == college)


def resolve_pair(college, course):
    college, course = str(college or '').strip(), str(course or '').strip()
    if college in CATALOG:
        college = CATALOG[college][0]
    key = course if course in COURSES else ALIASES.get(course.upper())
    pair = COURSES.get(key) if key else next((p for p in COURSES.values() if p[1].casefold() == course.casefold()), None)
    if not pair or (college and college.casefold() != pair[0].casefold()):
        return None
    return pair


def validate_pair(college, course):
    if not str(college or '').strip():
        raise ValueError('Select college.')
    pair = resolve_pair(college, course)
    if not pair:
        raise ValueError('Select a course belonging to the selected college.')
    return pair


def year_label(number):
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix} Year'


YEAR_LEVELS = tuple(year_label(n) for n in range(1, 7)) + (NOT_APPLICABLE,)


def normalize_year(value):
    text = str(value or '').strip()
    if text.casefold() == NOT_APPLICABLE.casefold():
        return NOT_APPLICABLE
    match = re.fullmatch(r'([1-9]\d*)(?:st|nd|rd|th)?(?:\s*(?:year|yr))?', text, re.I)
    if not match:
        raise ValueError('year_level must be a positive year number or Not applicable.')
    return year_label(int(match[1]))


def legacy_year_section(raw):
    text = str(raw or '').strip()
    words = {'first': '1', 'second': '2', 'third': '3', 'fourth': '4'}
    text = re.sub(r'\b(first|second|third|fourth)\b', lambda m: words[m[0].lower()], text, flags=re.I)
    match = re.fullmatch(r'(?:year\s+)?([1-9]\d?)(?:st|nd|rd|th)?\s*(?:year|yr)?\s*(?:[-/,]\s*)?(?:section\s+)?([A-Za-z]{1,3}\d*)?', text, re.I)
    if match:
        return year_label(int(match[1])), (match[2] or '').upper()
    if text.casefold() == NOT_APPLICABLE.casefold():
        return NOT_APPLICABLE, ''
    return '', ''


def academic_values(college, course, year, section):
    college, course = validate_pair(college, course)
    year = normalize_year(year)
    section = str(section or '').strip()
    return dict(college_department=college, course=course, report_year_level=year,
                report_section=section, year_and_section=' - '.join(v for v in (year, section) if v))


def display_academics(student):
    student = dict(student)
    pair = resolve_pair(student.get('college_department'), student.get('course'))
    year, section = legacy_year_section(student.get('year_and_section'))
    return dict(college_department=pair[0] if pair else NEEDS_REVIEW,
                course=pair[1] if pair else NEEDS_REVIEW,
                report_year_level=student.get('report_year_level') or year,
                report_section=student.get('report_section') or section)
