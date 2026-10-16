"""
Shared parsing / importing for the CBT Examination question bank.
Used by the teacher upload page (views.py).

Accepted layouts
  * CSV / Excel (.xlsx) with a header row:
        subject, question, option_a, option_b, option_c, option_d, answer, explanation
    ("subject" and "explanation" are optional; "subject" may be left out when the teacher picks
    one subject for the whole file.)
  * Text file / pasted text, one question per line, fields separated by "|":
        question | A | B | C | D | answer letter [| explanation]
"""
import csv
import io
from collections import defaultdict

from django.db import transaction

from .models import ExamQuestion

MAX_ROWS = 2000

_HEADERS = {
    'subject': 'subject',
    'question': 'text', 'text': 'text',
    'a': 'a', 'option a': 'a', 'option_a': 'a', 'optiona': 'a',
    'b': 'b', 'option b': 'b', 'option_b': 'b', 'optionb': 'b',
    'c': 'c', 'option c': 'c', 'option_c': 'c', 'optionc': 'c',
    'd': 'd', 'option d': 'd', 'option_d': 'd', 'optiond': 'd',
    'answer': 'answer', 'correct': 'answer', 'correct answer': 'answer', 'correct option': 'answer',
    'explanation': 'explanation',
}


def _decode(raw):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def rows_from_text(text, pipe=None):
    """Split pasted / text-file content into rows of cells."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    if pipe is None:
        pipe = lines[0].count('|') >= 5
    if pipe:
        return [[c.strip() for c in l.split('|')] for l in lines]
    delim = max((',', '\t', ';'), key=lines[0].count)
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)
            if any(c.strip() for c in r)]


def rows_from_upload(filename, raw):
    """Rows of cells from an uploaded .csv / .txt / .xlsx file. Raises ValueError with a friendly message."""
    name = (filename or '').lower()
    if name.endswith('.xlsx'):
        try:
            import openpyxl
        except ImportError:
            raise ValueError('Excel files need the "openpyxl" package on the server. '
                             'Save the sheet as CSV (File > Save As > CSV) and upload that instead.')
        try:
            ws = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
            return [[('' if c is None else str(c)).strip() for c in row] for row in ws.iter_rows(values_only=True)
                    if any(c not in (None, '') for c in row)]
        except Exception:
            raise ValueError('That Excel file could not be read. Save it as CSV and try again.')
    if not name.endswith(('.csv', '.txt', '.tsv')):
        raise ValueError('Please upload a .csv, .xlsx or .txt file.')
    return rows_from_text(_decode(raw), pipe=True if name.endswith('.txt') else None)


def clean_question(subject, text, a, b, c, d, answer, explanation, valid_subjects):
    """Validate one question. Returns (dict, None) or (None, error message)."""
    canon = {s.lower(): s for s in valid_subjects}
    subj = canon.get((subject or '').strip().lower())
    if not subj:
        return None, ('no subject given' if not (subject or '').strip()
                      else f'unknown subject "{subject.strip()[:40]}"')
    text = (text or '').strip()
    opts = [(x or '').strip() for x in (a, b, c, d)]
    if not text:
        return None, 'the question is empty'
    if not all(opts):
        return None, 'all four options (A-D) are required'
    if len({o.lower() for o in opts}) < 4:
        return None, 'two options are identical'
    if any(len(o) > 300 for o in opts):
        return None, 'an option is longer than 300 characters'
    ans = (answer or '').strip().upper().replace('OPTION', '').strip()
    ans = {'1': 'A', '2': 'B', '3': 'C', '4': 'D'}.get(ans, ans)
    if ans not in ('A', 'B', 'C', 'D'):
        return None, 'the answer must be A, B, C or D'
    return dict(subject_name=subj, text=text, a=opts[0], b=opts[1], c=opts[2], d=opts[3],
                answer=ans, explanation=(explanation or '').strip()), None


def parse_rows(rows, valid_subjects, default_subject=''):
    """
    Turn rows of cells into validated question dicts.
    Returns (questions, errors) where errors is a list of (line number, message).
    """
    questions, errors, seen = [], [], set()
    rows = [r for r in rows if any((c or '').strip() for c in r)]
    colmap, first_line = None, 1
    if rows:
        head = [(c or '').strip().lower() for c in rows[0]]
        if 'question' in head or 'text' in head:
            colmap = {_HEADERS[h]: i for i, h in enumerate(head) if h in _HEADERS}
            rows, first_line = rows[1:], 2
            missing = [k for k in ('text', 'a', 'b', 'c', 'd', 'answer') if k not in colmap]
            if missing:
                return [], [(1, 'header row is missing: ' + ', '.join(missing))]

    for line, row in enumerate(rows, start=first_line):
        row = [(c or '').strip() for c in row]
        if colmap is not None:
            g = lambda k: row[colmap[k]] if k in colmap and colmap[k] < len(row) else ''
            fields = (g('subject') or default_subject, g('text'), g('a'), g('b'), g('c'), g('d'),
                      g('answer'), g('explanation'))
        elif default_subject and len(row) in (6, 7):
            fields = (default_subject,) + tuple(row[:6]) + ((row[6],) if len(row) == 7 else ('',))
        elif len(row) in (7, 8):
            fields = tuple(row[:7]) + ((row[7],) if len(row) == 8 else ('',))
        else:
            errors.append((line, 'expected: question | A | B | C | D | answer'
                                 + ('' if default_subject else ' (and a subject)')))
            continue
        q, err = clean_question(*fields, valid_subjects)
        if err:
            errors.append((line, err))
            continue
        key = (q['subject_name'], q['text'].lower(), q['a'].lower(), q['b'].lower(), q['c'].lower(), q['d'].lower())
        if key in seen:
            errors.append((line, 'duplicate of an earlier line in this file'))
            continue
        seen.add(key)
        questions.append(q)
    return questions, errors


@transaction.atomic
def import_questions(questions, replace=False):
    """
    Save validated questions. Safe to repeat: a question is identified by its subject, text and options.
    With replace=True, each subject in the file ends up with exactly the uploaded questions
    (papers already taken keep their own snapshot, so results and reviews are unaffected).
    Returns (created, already_present, removed).
    """
    created = present = removed = 0
    ids = defaultdict(list)
    for q in questions:
        fp = ExamQuestion.make_fingerprint(q['subject_name'], q['text'], q['a'], q['b'], q['c'], q['d'])
        defaults = dict(subject_name=q['subject_name'], text=q['text'], option_a=q['a'], option_b=q['b'],
                        option_c=q['c'], option_d=q['d'], answer=q['answer'], is_active=True)
        if q['explanation']:
            defaults['explanation'] = q['explanation']
        obj, was_created = ExamQuestion.objects.update_or_create(fingerprint=fp, defaults=defaults)
        ids[q['subject_name']].append(obj.pk)
        created += was_created
        present += (not was_created)
    if replace:
        for subject, pks in ids.items():
            stale = ExamQuestion.objects.filter(subject_name=subject).exclude(pk__in=pks)
            removed += stale.count()
            stale.delete()
    return created, present, removed
