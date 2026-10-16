"""
Shared parsing / importing for the CBT Examination question bank (used by the teacher upload page).

Accepted layouts
  * CSV / Excel (.xlsx) with a header row. Columns (names are not case sensitive):
        subject, question, option_a, option_b, option_c, option_d, answer, explanation,
        image,            <- file name of a picture shown with this question (uploaded in the same step)
        passage,          <- a label (P1, P2 ...) shared by all questions that use the same information
        passage_title, passage_text, passage_image   <- describe the shared information once, on the first
                                                        question of the group
    Only question, option_a-d and answer are required ("subject" may be left out when one subject is chosen).
  * Text file / pasted text, one question per line, fields separated by "|":
        question | A | B | C | D | answer letter [| explanation]
    (text lines cannot carry images or passages; use a spreadsheet for those)
"""
import csv
import io
from collections import defaultdict

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from .models import ExamPassage, ExamQuestion

MAX_ROWS = 2000
MAX_IMAGES = 200
MAX_IMAGE_BYTES = 3 * 1024 * 1024
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.gif': 'image/gif', '.webp': 'image/webp'}

_HEADERS = {
    'subject': 'subject',
    'question': 'text', 'text': 'text',
    'a': 'a', 'option_a': 'a', 'optiona': 'a',
    'b': 'b', 'option_b': 'b', 'optionb': 'b',
    'c': 'c', 'option_c': 'c', 'optionc': 'c',
    'd': 'd', 'option_d': 'd', 'optiond': 'd',
    'answer': 'answer', 'correct': 'answer', 'correct_answer': 'answer', 'correct_option': 'answer',
    'explanation': 'explanation',
    'image': 'image', 'diagram': 'image', 'picture': 'image',
    'passage': 'passage', 'passage_ref': 'passage',
    'passage_title': 'passage_title', 'passage_text': 'passage_text',
    'passage_image': 'passage_image', 'passage_diagram': 'passage_image',
}


# ───────────────────────────── images ─────────────────────────────

def _looks_like_image(data):
    return (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')
            or data.startswith((b'GIF87a', b'GIF89a')) or (data[:4] == b'RIFF' and data[8:12] == b'WEBP'))


def check_image(name, data):
    """Return an error message, or None if this is an acceptable picture."""
    ext = '.' + name.lower().rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in IMAGE_TYPES:
        return f'"{name}": only PNG, JPG, GIF or WEBP pictures are allowed'
    if len(data) > MAX_IMAGE_BYTES:
        return f'"{name}" is larger than 3 MB'
    if not _looks_like_image(data):
        return f'"{name}" is not a valid picture'
    return None


def image_blobs_from_uploads(files):
    """
    Read the pictures a teacher attached to an upload.
    Returns ({lower-case file name: (name, bytes, content_type)}, [error messages]).
    """
    blobs, errors = {}, []
    for f in files[:MAX_IMAGES]:
        name = (f.name or '').replace('\\', '/').rsplit('/', 1)[-1]
        data = f.read()
        err = check_image(name, data)
        if err:
            errors.append(err)
            continue
        blobs[name.lower()] = (name, data, IMAGE_TYPES['.' + name.lower().rsplit('.', 1)[-1]])
    if len(files) > MAX_IMAGES:
        errors.append(f'Only the first {MAX_IMAGES} pictures were read')
    return blobs, errors


def _file(blob):
    name, data, ctype = blob
    return SimpleUploadedFile(name, data, content_type=ctype)


# ───────────────────────────── reading rows ─────────────────────────────

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


# ───────────────────────────── validation ─────────────────────────────

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


def parse_rows(rows, valid_subjects, default_subject='', images=None):
    """
    Turn rows of cells into validated question dicts.
    `images` is the {file name: blob} dict from image_blobs_from_uploads.
    Returns (questions, errors); errors is a list of (line number, message).
    """
    images = images or {}
    questions, errors, seen = [], [], set()
    rows = [r for r in rows if any((c or '').strip() for c in r)]
    colmap, first_line = None, 1
    if rows:
        head = [(c or '').strip().lower().replace(' ', '_').replace('-', '_') for c in rows[0]]
        if 'question' in head or 'text' in head:
            colmap = {}
            for i, h in enumerate(head):
                if h in _HEADERS and _HEADERS[h] not in colmap:
                    colmap[_HEADERS[h]] = i
            rows, first_line = rows[1:], 2
            missing = [k for k in ('text', 'a', 'b', 'c', 'd', 'answer') if k not in colmap]
            if missing:
                return [], [(1, 'header row is missing: ' + ', '.join(missing))]

    def find_image(fname, line, label):
        if not fname:
            return None, None
        blob = images.get(fname.replace('\\', '/').rsplit('/', 1)[-1].lower())
        if blob is None:
            return None, f'{label} "{fname}" was not attached to this upload (add it under "Pictures")'
        return blob, None

    for line, row in enumerate(rows, start=first_line):
        row = [(c or '').strip() for c in row]
        extra = {}
        if colmap is not None:
            g = lambda k: row[colmap[k]] if k in colmap and colmap[k] < len(row) else ''
            fields = (g('subject') or default_subject, g('text'), g('a'), g('b'), g('c'), g('d'),
                      g('answer'), g('explanation'))
            extra = {k: g(k) for k in ('image', 'passage', 'passage_title', 'passage_text', 'passage_image')}
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

        q['image_blob'], err = find_image(extra.get('image', ''), line, 'picture')
        if not err:
            q['passage_blob'], err = find_image(extra.get('passage_image', ''), line, 'passage picture')
        if err:
            errors.append((line, err))
            continue
        q.update(passage_key=extra.get('passage', ''), passage_title=extra.get('passage_title', ''),
                 passage_text=extra.get('passage_text', ''), line=line, order=len(questions))

        key = (q['subject_name'], q['text'].lower(), q['a'].lower(), q['b'].lower(), q['c'].lower(), q['d'].lower())
        if key in seen:
            errors.append((line, 'duplicate of an earlier line in this file'))
            continue
        seen.add(key)
        questions.append(q)
    return questions, errors


# ───────────────────────────── saving ─────────────────────────────

@transaction.atomic
def import_questions(questions, replace=False):
    """
    Save validated questions. Safe to repeat: a question is identified by its subject, text and options.
    Questions that share a `passage` label (within one subject) are attached to the same passage.
    With replace=True, each subject in the file ends up with exactly the uploaded questions
    (papers already taken keep their own snapshot, so results and reviews are unaffected).
    Returns (created, already_present, removed, errors) where errors is a list of (line, message).
    """
    created = present = removed = 0
    errors, ids, passages = [], defaultdict(list), {}

    for q in questions:
        passage = None
        key = (q.get('passage_key') or '').strip()
        if key:
            pkey = (q['subject_name'], key.lower())
            passage = passages.get(pkey)
            if passage is None:
                passage = ExamPassage.objects.filter(subject_name=q['subject_name'], ref__iexact=key).first()
                has_content = q.get('passage_text') or q.get('passage_blob')
                if passage is None and not has_content:
                    errors.append((q.get('line', 0), f'passage "{key}" has no text or picture. '
                                                     'Fill passage_text / passage_image on the first question of the group'))
                    continue
                if passage is None:
                    passage = ExamPassage(subject_name=q['subject_name'], ref=key)
                if q.get('passage_title'):
                    passage.title = q['passage_title']
                if q.get('passage_text'):
                    passage.text = q['passage_text']
                if q.get('passage_blob'):
                    passage.image = _file(q['passage_blob'])
                passage.save()
                passages[pkey] = passage

        fp = ExamQuestion.make_fingerprint(q['subject_name'], q['text'], q['a'], q['b'], q['c'], q['d'])
        defaults = dict(subject_name=q['subject_name'], text=q['text'], option_a=q['a'], option_b=q['b'],
                        option_c=q['c'], option_d=q['d'], answer=q['answer'], is_active=True)
        if q.get('explanation'):
            defaults['explanation'] = q['explanation']
        if passage is not None:
            defaults.update(passage=passage, order=q.get('order', 0))
        if q.get('image_blob'):
            defaults['image'] = _file(q['image_blob'])
        obj, was_created = ExamQuestion.objects.update_or_create(fingerprint=fp, defaults=defaults)
        ids[q['subject_name']].append(obj.pk)
        created += was_created
        present += (not was_created)

    if replace and errors:
        transaction.set_rollback(True)      # a replace must never half-apply
        return 0, 0, 0, errors
    if replace:
        for subject, pks in ids.items():
            stale = ExamQuestion.objects.filter(subject_name=subject).exclude(pk__in=pks)
            removed += stale.count()
            stale.delete()
    return created, present, removed, errors
