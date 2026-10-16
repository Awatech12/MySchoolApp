from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, auth
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from school.exam_import import (MAX_ROWS as EXAM_IMPORT_MAX_ROWS, check_image, clean_question, image_blobs_from_uploads,
                                import_questions, parse_rows, rows_from_text, rows_from_upload)
from school.models import Profile, Post, PostComment, Notification, LiveSession, LiveParticipant, CBTExam, CBTScore, ClassPost, ClassPostComment, RegisteredSubject, ClassPostReport, ExamQuestion, ExamPassage, ExaminationAttempt
from django.db.models import Q, F, Max, DateTimeField
from django.db.models.functions import Coalesce
import uuid, json, re, logging, random
from datetime import date as date_type
from datetime import datetime as datetime_type, time as time_type
from django.db import transaction, IntegrityError
from django.views.decorators.cache import never_cache
import csv, io, secrets
from collections import Counter
from datetime import timedelta
from functools import wraps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.urls import reverse
from django.core.validators import validate_email
from django.db.models import Avg, Count
from django.http import HttpResponse, Http404, HttpResponseBadRequest
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.timezone import localtime
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


# ── Senior secondary subject list (Nigerian curriculum) ─────────────────
# Keep this in sync with the SENIOR_SUBJECTS array in
# register.html's <script> block — they are duplicated there so the tick
# list can render instantly without an extra round-trip to the server.
SENIOR_SECONDARY_SUBJECTS = [
    'Mathematics', 'English Language', 'Physics', 'Chemistry', 'Biology',
    'Economics', 'Government', 'Literature in English', 'Geography',
    'Further Mathematics', 'Agricultural Science', 'Accounting', 'Commerce',
    'Christian Religious Studies', 'Islamic Religious Studies', 'Civic Education',
    'Computer Studies', 'French', 'Technical Drawing', 'Food and Nutrition',
]

SUBJECTS_BY_CATEGORY = {
    'senior': SENIOR_SECONDARY_SUBJECTS,
}

# Display grouping for the Edit Subjects modal. Only used for layout: the list
# above stays the single source of truth for validation, and any subject that
# is not in a group below is shown under "Other" automatically.
SUBJECT_GROUPS = [
    ('Core subjects',          ['English Language', 'Mathematics', 'Civic Education', 'Computer Studies']),
    ('Sciences',               ['Physics', 'Chemistry', 'Biology', 'Further Mathematics', 'Agricultural Science']),
    ('Arts & Social Sciences', ['Literature in English', 'Government', 'Geography',
                                'Christian Religious Studies', 'Islamic Religious Studies', 'French']),
    ('Commercial & Vocational', ['Economics', 'Accounting', 'Commerce', 'Technical Drawing', 'Food and Nutrition']),
]

CLASS_LEVEL_CHOICES = ['SS1', 'SS2', 'SS3']
GENDER_CHOICES      = ['Male', 'Female', 'Non-binary', 'Prefer not to say']
CAREER_SUGGESTIONS  = [
    'Doctor', 'Nurse', 'Engineer', 'Lawyer', 'Teacher', 'Pilot', 'Accountant',
    'Scientist', 'Software Developer', 'Architect', 'Banker', 'Entrepreneur',
    'Pharmacist', 'Journalist',
]

# Grade -> short remark shown beside CBT results (system generated, not a teacher comment)
GRADE_REMARKS = {
    'distinction': 'Excellent work',
    'credit':      'Good performance',
    'pass':        'Satisfactory — keep practising',
    'nearly':      'Almost there — review weak topics',
    'fail':        'Needs more revision',
}

# Maximum number of exam rows loaded into the profile's Examination Results tab.
EXAM_HISTORY_LIMIT = 500

MAX_PICTURE_BYTES = 5 * 1024 * 1024
ALLOWED_PICTURE_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')
_PHONE_RE = re.compile(r'^\+?[0-9][0-9\s\-()]{6,18}[0-9]$')


def _normalize_class_level(value):
    """
    Return 'SS1'/'SS2'/'SS3' (optionally with an arm letter, e.g. 'SS2A') for any
    Senior Secondary class label, or None if the value is not a senior class.
    """
    v = re.sub(r'\s+', ' ', (value or '').strip().upper())
    m = re.match(r'^(?:SSS?|SENIOR(?: SECONDARY)?(?: SCHOOL)?)\s*-?\s*([1-3])\s*([A-Z]?)$', v)
    if m:
        return f'SS{m.group(1)}{m.group(2)}'
    m = re.match(r'^(?:YEAR|GRADE)\s*(10|11|12)$', v)
    if m:
        return f'SS{int(m.group(1)) - 9}'
    return None


def _can_view_private(request, target_user):
    """Personal details and full academic records: the student themself or staff only."""
    u = request.user
    return u.is_authenticated and (u.pk == target_user.pk or u.is_staff or u.is_superuser)


def _as_local(dt):
    """A datetime in the site's timezone (naive datetimes are returned unchanged)."""
    return timezone.localtime(dt) if timezone.is_aware(dt) else dt


def _parse_iso_date(value):
    """Parse a strict YYYY-MM-DD string into a date, or return None if it is not a real date."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date_type.fromisoformat(value)
    except ValueError:
        return None


def _day_bounds(day):
    """[start, end) of a calendar day in the site's timezone, as aware datetimes."""
    start = datetime_type.combine(day, time_type.min)
    end   = datetime_type.combine(day + timedelta(days=1), time_type.min)
    if timezone.is_naive(start):
        start, end = timezone.make_aware(start), timezone.make_aware(end)
    return start, end


def _valid_picture(upload):
    """Return an error string if the uploaded picture is unacceptable, else None."""
    if upload.content_type not in ALLOWED_PICTURE_TYPES:
        return 'Profile picture must be a JPG, PNG, WEBP or GIF image.'
    if upload.size > MAX_PICTURE_BYTES:
        return 'Profile picture must be 5 MB or smaller.'
    try:
        from PIL import Image
        Image.open(upload).verify()
        upload.seek(0)
    except ImportError:
        pass
    except Exception:
        return 'That file is not a valid image.'
    return None


def _apply_profile_updates(user, profile, data, files):
    """
    Validate and apply the Edit Profile form. Returns an error string, or None on
    success. Only fields present in the submitted data are touched, so partial
    posts can never wipe other fields.
    """
    def text(key, max_len, label):
        raw = (data.get(key) or '').strip()
        if len(raw) > max_len:
            raise ValueError(f'{label} must be {max_len} characters or fewer.')
        return raw

    try:
        updates = {}
        if 'fname' in data:
            updates['first_name'] = text('fname', 50, 'First name')
            if not updates['first_name']:
                return 'First name cannot be empty.'
        if 'lname' in data:
            updates['last_name'] = text('lname', 50, 'Last name')
            if not updates['last_name']:
                return 'Last name cannot be empty.'

        p_updates = {}
        if 'bio' in data:      p_updates['bio']      = text('bio', 300, 'Bio')
        if 'location' in data: p_updates['location'] = text('location', 300, 'Location')
        if 'address' in data:  p_updates['address']  = text('address', 200, 'Address')
        if 'school' in data:   p_updates['school']   = text('school', 200, 'School')
        if 'career_goal' in data:
            p_updates['career_goal'] = text('career_goal', 100, 'Career goal')

        if 'phone' in data:
            phone = text('phone', 20, 'Phone number')
            if phone and not _PHONE_RE.match(phone):
                return 'Please enter a valid phone number.'
            p_updates['phone'] = phone

        if 'gender' in data:
            raw_gender = (data.get('gender') or '').strip()
            if raw_gender and raw_gender not in GENDER_CHOICES:
                return 'Please choose a valid gender option.'
            p_updates['gender'] = raw_gender

        if 'specialization' in data:
            spec = (data.get('specialization') or '').strip()
            valid_specs = [c[0] for c in Profile.SPECIALIZATION_CHOICES]
            if spec and spec not in valid_specs:
                return 'Please choose a valid specialization.'
            p_updates['specialization'] = spec

        if 'class_level' in data:
            raw_class = (data.get('class_level') or '').strip()
            if raw_class:
                norm = _normalize_class_level(raw_class)
                if not norm:
                    return 'Class / Level must be a Senior Secondary class (SS1, SS2 or SS3).'
                p_updates['class_level'] = norm
            else:
                p_updates['class_level'] = ''

        if 'date_of_birth' in data:
            dob = (data.get('date_of_birth') or '').strip()
            if dob:
                try:
                    parsed = date_type.fromisoformat(dob)
                except ValueError:
                    return 'Please enter a valid date of birth.'
                today = date_type.today()
                if parsed >= today or parsed.year < today.year - 100:
                    return 'Please enter a realistic date of birth.'
                p_updates['date_of_birth'] = parsed
            else:
                p_updates['date_of_birth'] = None

        image = files.get('image')
        if image:
            err = _valid_picture(image)
            if err:
                return err
            p_updates['picture'] = image
    except ValueError as exc:
        return str(exc)

    with transaction.atomic():
        for attr, val in updates.items():
            setattr(user, attr, val)
        for attr, val in p_updates.items():
            setattr(profile, attr, val)
        profile.save()   # Profile.save() also saves the related user
    return None



# Matches TMTC/2026/0007 (also tolerates tmtc-2026-0007, "TMTC 2026 0007", TMTC20260007)
_ADMISSION_RE = re.compile(r'^TMTC[\s/\-_]*(\d{4})[\s/\-_]*(\d+)$', re.IGNORECASE)


def _login_candidates(identifier):
    """
    Turn whatever the person typed into the list of usernames to try:
    username, email, or Admission No (TMTC/<year>/<4-digit id>).
    The Admission No is computed from Profile.created_at + user id (not stored),
    so it is parsed back into a user id and verified against the profile.
    """
    identifier = (identifier or '').strip()
    candidates = []
    if not identifier:
        return candidates

    # 1) Username exactly as typed
    candidates.append(identifier)

    # 2) Admission No
    m = _ADMISSION_RE.match(identifier)
    if m:
        year, user_id = int(m.group(1)), int(m.group(2))
        profile = Profile.objects.select_related('user').filter(user_id=user_id).first()
        if profile and profile.created_at and profile.created_at.year == year:
            candidates.append(profile.user.username)

    # 3) Email (case-insensitive; email is not unique in Django's User, so try each match)
    if '@' in identifier:
        candidates.extend(
            User.objects.filter(email__iexact=identifier).values_list('username', flat=True)
        )

    # de-duplicate, keep order
    return list(dict.fromkeys(candidates))


def index(request):
    if request.user.is_authenticated:
        messages.info(request, f'Welcome back {request.user.username}')
        return redirect('home')
    if request.method == 'POST':
        user_check = request.POST.get('user_check')
        password   = request.POST.get('password')
        user = None
        for username in _login_candidates(user_check):
            user = authenticate(request, username=username, password=password)
            if user is not None:
                break
        if user is not None:
            login(request, user)
            messages.info(request, f'Welcome back {user.username}')
            request.session.set_expiry(None)
            return redirect(request.GET.get('next', 'home'))
        else:
            messages.info(request, 'Check Login details')
            return redirect('/')
    return render(request, 'index.html')


def register(request):
    if request.method == 'POST':
        username  = request.POST.get('username')
        email     = request.POST.get('email')
        fname     = request.POST.get('fname')
        lname     = request.POST.get('lname')
        password  = request.POST.get('pass1')
        password2 = request.POST.get('pass2')
        if len(username) < 5:
            messages.info(request, 'Username Characters Can not Less than 5 characters')
            return redirect('register')
        elif User.objects.filter(username=username).exists():
            messages.info(request, 'Username is taken Alredy')
            return redirect('register')
        elif User.objects.filter(email=email).exists():
            messages.info(request, 'Email is taken Alredy')
            return redirect('register')
        elif password != password2:
            messages.info(request, 'Password not Match')
            return redirect('register')
        else:
            user = User.objects.create_user(
                username=username, email=email,
                first_name=fname, last_name=lname, password=password
            )
            Profile.objects.create(user=user)
            CBTScore.objects.create(user=user)        # ← create score row on register
            messages.info(request, "You Successfully joined Login Now")
            return redirect('/')
    return render(request, 'register.html')


@require_POST
def register_with_picture(request):
   
    username  = request.POST.get('username', '').strip()
    email     = request.POST.get('email', '').strip()
    fname     = request.POST.get('fname', '').strip()
    lname     = request.POST.get('lname', '').strip()
    password  = request.POST.get('pass1', '')
    password2 = request.POST.get('pass2', '')
    picture   = request.FILES.get('profile_picture')

    student_category  = 'senior'  # platform is Senior Secondary only
    selected_subjects = [s.strip() for s in request.POST.getlist('subjects') if s.strip()]
    career_goal        = request.POST.get('career_goal', '').strip()

    # --- Server-side validation ---
    if len(username) < 5:
        return JsonResponse({'success': False, 'error': 'Username must be at least 5 characters.'})
    if User.objects.filter(username=username).exists():
        return JsonResponse({'success': False, 'error': 'Username is already taken.'})
    if User.objects.filter(email=email).exists():
        return JsonResponse({'success': False, 'error': 'Email is already registered.'})
    if password != password2:
        return JsonResponse({'success': False, 'error': 'Passwords do not match.'})
    if len(password) < 6:
        return JsonResponse({'success': False, 'error': 'Password must be at least 6 characters.'})
    if not selected_subjects:
        return JsonResponse({'success': False, 'error': 'Please select at least one subject to register for.'})
    if not career_goal:
        return JsonResponse({'success': False, 'error': 'Please tell us what you want to become in the future.'})

    # --- Create user ---
    user = User.objects.create_user(
        username=username, email=email,
        first_name=fname, last_name=lname, password=password
    )
    profile = Profile.objects.create(
        user=user,
        student_category=student_category,
        career_goal=career_goal,
    )
    CBTScore.objects.create(user=user)

    # --- Save profile picture if provided ---
    if picture:
        allowed_types = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
        if picture.content_type in allowed_types and picture.size <= 5 * 1024 * 1024:
            profile.picture = picture
            profile.save()

    # --- Save the ticked subjects (only ones that are valid for the chosen category) ---
    valid_subjects = SUBJECTS_BY_CATEGORY[student_category]
    RegisteredSubject.objects.bulk_create(
        [
            RegisteredSubject(student=user, category=student_category, subject_name=name)
            for name in selected_subjects if name in valid_subjects
        ],
        ignore_conflicts=True,
    )

    # --- Log the user in immediately ---
    from django.contrib.auth import login as auth_login
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    auth_login(request, user)

    return JsonResponse({'success': True, 'redirect': '/home', 'admission_number': profile.admission_number})


def _get_student_level(points):
    if points >= 2000:
        return ('Champion', 6)
    elif points >= 1000:
        return ('Platinum', 5)
    elif points >= 500:
        return ('Gold', 4)
    elif points >= 200:
        return ('Silver', 3)
    elif points >= 50:
        return ('Bronze', 2)
    else:
        return ('Beginner', 1)


def home(request):
    return render(request, 'home.html')


@login_required
@never_cache
def profile(request, username):
    """
    Student profile dashboard.

    Any signed-in user may open a profile and see the public summary (name, class,
    school, scores summary, subject performance). Personal details (phone, address,
    date of birth ...) and the full examination history are only put in the
    context for the student themself, or for staff.
    """
    from django.db.models import Max, Avg, Count, Sum

    user    = get_object_or_404(User, username=username)
    profile = get_object_or_404(Profile, user=user)

    is_owner         = request.user.pk == user.pk
    can_view_private = _can_view_private(request, user)

    # ── CBT aggregates ────────────────────────────────────────────
    cbt_score, _ = CBTScore.objects.get_or_create(user=user)
    all_exams     = CBTExam.objects.filter(student=user).order_by('-taken_at')

    totals = all_exams.aggregate(
        total_exams=Count('exam_id'),
        avg_score=Avg('percentage'),
        total_correct=Sum('score'),
        total_questions=Sum('total'),
        highest_score=Max('percentage'),
    )

    total_exams_taken  = totals['total_exams']  or 0
    avg_score          = round(totals['avg_score'] or 0)
    highest_score      = round(totals['highest_score'] or 0)
    total_points       = cbt_score.points
    subjects_attempted = all_exams.values('subject').distinct().count()

    # ── Per-subject summary (one grouped query instead of one per subject) ──
    subject_labels = dict(CBTExam.SUBJECT_CHOICES)
    per_subject = (
        all_exams.order_by().values('subject')
        .annotate(avg_pct=Avg('percentage'), best_pct=Max('percentage'), exams_taken=Count('exam_id'))
    )
    subject_summaries = [{
        'name':        subject_labels.get(row['subject'], row['subject'].capitalize()),
        'avg_pct':     round(row['avg_pct']  or 0),
        'best_pct':    round(row['best_pct'] or 0),
        'exams_taken': row['exams_taken'] or 0,
    } for row in per_subject]
    subject_summaries.sort(key=lambda x: x['avg_pct'], reverse=True)

    # ── Leaderboard rank (overall points) ────────────────────────
    best_rank = None
    best_rank_subject = None
    if total_points > 0:
        higher = CBTScore.objects.filter(points__gt=total_points).count()
        best_rank = higher + 1

    # ── Examination history (private: owner / staff only) ────────
    exam_list = list(all_exams[:EXAM_HISTORY_LIMIT]) if can_view_private else []

    def _band(pct):
        return 'high' if pct >= 70 else 'mid' if pct >= 50 else 'low'

    exam_history = []
    for ex in exam_list:
        subject_label = subject_labels.get(ex.subject, ex.subject.replace('_', ' ').title())
        # "feedback"/"title" are optional extras: shown automatically if you
        # ever add such fields to CBTExam, without needing any change here.
        teacher_feedback = (getattr(ex, 'feedback', '') or getattr(ex, 'teacher_feedback', '') or '').strip()
        test_name = (getattr(ex, 'title', '') or getattr(ex, 'test_name', '')
                     or f'{subject_label} ' + ('CBT Examination' if ex.exam_type == 'examination' else 'CBT Test'))
        taken_local = _as_local(ex.taken_at)
        exam_history.append({
            'test_name':        test_name,
            'exam_type':        ex.exam_type,
            'exam_type_display': ex.get_exam_type_display(),
            'subject':          subject_label,
            'date':             ex.taken_at,
            'date_iso':         taken_local.strftime('%Y-%m-%d'),   # calendar day in the site's timezone
            'score':            ex.score,
            'total':            ex.total,
            'percentage':       ex.percentage,
            'band':             _band(ex.percentage),
            'grade':            ex.grade,
            'grade_display':    ex.get_grade_display(),
            'time_display':     ex.time_display if ex.time_used_sec else '',
            'teacher_feedback': teacher_feedback,
            'remark':           GRADE_REMARKS.get(ex.grade, ''),
        })

    # ── Test dates offered by the date filter / "Print Test Results" ──
    # exam_history is newest-first, so each day's rows are contiguous. If the history
    # was cut at the limit, the oldest day may be incomplete: it is not offered for
    # filtering (printing always re-queries the database, so it is never partial).
    exam_days = []
    for row in exam_history:
        if not exam_days or exam_days[-1]['date_iso'] != row['date_iso']:
            exam_days.append({'date_iso': row['date_iso'], 'count': 0})
        exam_days[-1]['count'] += 1
    if exam_days and len(exam_list) >= EXAM_HISTORY_LIMIT:
        exam_days.pop()
    for day in exam_days:
        day['date'] = _parse_iso_date(day['date_iso'])

    # legacy structure kept for any template still reading `scores`
    scores = []
    for ex in exam_list[:20]:
        scores.append({
            'subject':         type('_S', (), {'name': ex.subject.capitalize()}),
            'exam':            type('_E', (), {'title': f'{ex.subject.capitalize()} — {ex.score}/{ex.total}'}),
            'score':           ex.score,
            'total_questions': ex.total,
            'percentage':      ex.percentage,
            'created_at':      ex.taken_at,
            'grade':           ex.grade,
        })

    level_name, _ = _get_student_level(total_points)

    # ── Subjects the student registered ──────────────────────────
    registered_subjects = list(
        RegisteredSubject.objects.filter(student=user).order_by('subject_name')
    )
    registered_subject_names = [rs.subject_name for rs in registered_subjects]
    student_category_display = profile.get_student_category_display() if profile.student_category else ''

    summary_by_name = {s['name']: s for s in subject_summaries}
    cbt_labels      = {label for _key, label in CBTExam.SUBJECT_CHOICES}
    registered_subject_cards = []
    for rs in registered_subjects:
        stat = summary_by_name.get(rs.subject_name)
        registered_subject_cards.append({
            'name':        rs.subject_name,
            'has_cbt':     rs.subject_name in cbt_labels,
            'exams_taken': stat['exams_taken'] if stat else 0,
            'avg_pct':     stat['avg_pct'] if stat else None,
            'best_pct':    stat['best_pct'] if stat else None,
        })

    # ── Class level / subject picker (owner only) ────────────────
    current_class_level = _normalize_class_level(profile.class_level) or ''
    class_level_legacy  = bool(profile.class_level) and not current_class_level
    class_level_options = list(CLASS_LEVEL_CHOICES)
    if current_class_level and current_class_level not in class_level_options:
        class_level_options.append(current_class_level)

    subject_groups, selected_subject_names = [], []
    if is_owner:
        placed = set()
        for title, names in SUBJECT_GROUPS:
            items = [n for n in names if n in SENIOR_SECONDARY_SUBJECTS]
            placed.update(items)
            if items:
                subject_groups.append({'title': title, 'subjects': items})
        other = [n for n in SENIOR_SECONDARY_SUBJECTS if n not in placed]
        if other:
            subject_groups.append({'title': 'Other subjects', 'subjects': other})
        selected_subject_names = [n for n in registered_subject_names if n in SENIOR_SECONDARY_SUBJECTS]

    # Admission number is generated automatically at registration (see Profile.admission_number)
    admission_number = profile.admission_number

    context = {
        'user':               user,
        'profile':            profile,
        'is_owner':           is_owner,
        'can_view_private':   can_view_private,
        'registered_subjects':       registered_subjects,
        'registered_subject_names':  registered_subject_names,
        'registered_subject_cards':  registered_subject_cards,
        'registered_subject_count':  len(registered_subjects),
        'student_category_display':  student_category_display,
        'admission_number':          admission_number,
        # stats
        'total_exams_taken':  total_exams_taken,
        'avg_score':          avg_score,
        'highest_score':      highest_score,
        'total_points':       total_points,
        'subjects_attempted': subjects_attempted,
        # rank
        'best_rank':          best_rank,
        'best_rank_subject':  best_rank_subject,
        # tabs
        'scores':             scores,
        'exam_history':       exam_history,
        'exam_days':          exam_days,
        'exam_history_limit': EXAM_HISTORY_LIMIT,
        'subject_summaries':  subject_summaries,
        # level
        'level_name':         level_name,
        # edit forms (owner only is rendered in the template)
        'subject_groups':         subject_groups,
        'selected_subject_names': selected_subject_names,
        'current_class_level':    current_class_level,
        'class_level_legacy':     class_level_legacy,
        'class_level_options':    class_level_options,
        'gender_choices':         GENDER_CHOICES,
        'specialization_choices': [c[0] for c in Profile.SPECIALIZATION_CHOICES],
        'career_suggestions':     CAREER_SUGGESTIONS,
        # legacy (keep existing templates happy)
        'cbt_score':          cbt_score,
        'recent_exams':       exam_list[:5],
    }
    return render(request, 'profile.html', context)


@login_required
@never_cache
@require_GET
def print_test_results(request, username):
    """
    Printable A4 "Test Results" report for ONE test date (?date=YYYY-MM-DD).

    It is a standalone page (no sidebar, tabs or other profile content), so printing it
    can only ever output the report. Same privacy rule as the profile's Examination
    Results tab: the student themself or staff only. Results are re-queried here by
    calendar day in the site's timezone, never trusted from the browser.
    """
    target = get_object_or_404(User, username=username)
    if not _can_view_private(request, target):
        raise PermissionDenied

    day = _parse_iso_date(request.GET.get('date'))
    if day is None or day.year < 2000 or day.year > 2100:
        return HttpResponseBadRequest('Please choose a valid test date.')

    start, end = _day_bounds(day)
    exams = list(CBTExam.objects.filter(student=target, taken_at__gte=start, taken_at__lt=end))
    if not exams:
        raise Http404('No examination results were found for that date.')

    subject_labels = dict(CBTExam.SUBJECT_CHOICES)
    rows = []
    for ex in exams:
        label = subject_labels.get(ex.subject, ex.subject.replace('_', ' ').title())
        feedback = (getattr(ex, 'feedback', '') or getattr(ex, 'teacher_feedback', '') or '').strip()
        rows.append({
            'subject':       label,
            'score':         ex.score,
            'total':         ex.total,
            'percentage':    ex.percentage,
            'grade_display': ex.get_grade_display(),
            'exam_type':     ex.get_exam_type_display(),
            # Teacher's own feedback if the model ever stores it; otherwise the system remark.
            'feedback':      feedback or GRADE_REMARKS.get(ex.grade, ''),
        })
    rows.sort(key=lambda r: (r['subject'], -r['percentage']))

    got    = sum(r['score'] for r in rows)
    out_of = sum(r['total'] for r in rows)
    pct    = round(got / out_of * 100) if out_of else 0
    overall_grade = dict(CBTExam.GRADE_CHOICES).get(_grade_for(pct), '')

    profile = get_object_or_404(Profile, user=target)
    return render(request, 'test_results_print.html', {
        'student':          target,
        'profile':          profile,
        'admission_number': profile.admission_number,
        'test_date':        day,
        'rows':             rows,
        'subject_count':    len({r['subject'] for r in rows}),
        'total_score':      got,
        'total_questions':  out_of,
        'overall_pct':      pct,
        'overall_grade':    overall_grade,
        'autoprint':        request.GET.get('autoprint') == '1',
    })


@login_required
@never_cache
@require_GET
def id_card(request, username=None):
    """
    Printable Student ID card (front and back, CR80 / 85.6 x 54 mm).

    Students open their own card at /id-card/. Staff may open any student's card
    at /id-card/<username>/. Everyone else is refused (same privacy rule as the
    profile's private details).
    """
    target = request.user if username is None else get_object_or_404(User, username=username)
    if not _can_view_private(request, target):
        raise PermissionDenied

    profile = get_object_or_404(Profile, user=target)
    dob = profile.date_of_birth
    subjects = list(
        RegisteredSubject.objects.filter(student=target).values_list('subject_name', flat=True)
    )

    return render(request, 'id_card.html', {
        'student':          target,
        'profile':          profile,
        'full_name':        target.get_full_name() or target.username,
        'admission_number': profile.admission_number,
        'class_level':      _normalize_class_level(profile.class_level) or profile.class_level,
        'dob':              dob,
        'issue_year':       profile.created_at.year if profile.created_at else timezone.now().year,
        'subjects':         subjects,
        'autoprint':        request.GET.get('autoprint') == '1',
    })


@login_required
@require_POST
def change_password(request):
    """
    AJAX endpoint for the profile page's Change Password modal.
    Students can no longer edit their profile details (admin only); they can
    only change their own password.
    Accepts: current_password, new_password, confirm_password.
    Returns JSON { success, error? }.
    """
    user    = request.user
    current = request.POST.get('current_password', '')
    new_pw  = request.POST.get('new_password', '')
    confirm = request.POST.get('confirm_password', '')

    if not user.check_password(current):
        return JsonResponse({'success': False, 'error': 'Your current password is incorrect.'}, status=400)
    if len(new_pw) < 6:
        return JsonResponse({'success': False, 'error': 'New password must be at least 6 characters.'}, status=400)
    if len(new_pw) > 128:
        return JsonResponse({'success': False, 'error': 'New password is too long (max 128 characters).'}, status=400)
    if new_pw != confirm:
        return JsonResponse({'success': False, 'error': 'New passwords do not match.'}, status=400)
    if new_pw == current:
        return JsonResponse({'success': False, 'error': 'New password must be different from your current password.'}, status=400)

    user.set_password(new_pw)
    user.save(update_fields=['password'])
    update_session_auth_hash(request, user)   # keep the student signed in
    return JsonResponse({'success': True})


@login_required
@require_POST
def update_passport(request):
    """
    AJAX endpoint for the profile page's Update Passport modal.
    Students may only change their passport photograph (everything else on the
    profile is managed by the admin).
    Accepts multipart/form-data: image (file). Returns JSON { success, error?, url? }.
    """
    user    = request.user
    profile = user.profile
    image   = request.FILES.get('image')

    if not image:
        return JsonResponse({'success': False, 'error': 'Please choose a passport photo to upload.'}, status=400)
    err = _valid_picture(image)
    if err:
        return JsonResponse({'success': False, 'error': err}, status=400)

    try:
        profile.picture = image
        profile.save()
    except Exception:
        logger.exception('update_passport failed for user %s', user.pk)
        return JsonResponse({'success': False, 'error': 'Could not save your photo. Please try again.'}, status=500)
    return JsonResponse({'success': True, 'url': profile.get_picture_url()})


@login_required
@require_POST
def update_subjects(request):
    """
    AJAX endpoint for the profile page's Edit Subjects modal.
    Accepts: subjects (multi-value list). Category is always 'senior'.
    Replaces the signed-in student's RegisteredSubject rows with the new selection.
    """
    user     = request.user
    profile  = user.profile
    category = 'senior'  # platform is Senior Secondary only

    # Subjects must match the student's class level (Senior Secondary: SS1-SS3)
    if profile.class_level and not _normalize_class_level(profile.class_level):
        return JsonResponse({
            'success': False,
            'error': 'Your class level is not a Senior Secondary class. Please ask the admin to set it to SS1, SS2 or SS3.',
        }, status=400)

    submitted      = [s.strip() for s in request.POST.getlist('subjects') if s.strip()]
    valid_subjects = SUBJECTS_BY_CATEGORY[category]

    if any(name not in valid_subjects for name in submitted):
        return JsonResponse({'success': False, 'error': 'One or more selected subjects are not available for your class level.'}, status=400)

    clean_subjects = list(dict.fromkeys(submitted))   # de-duplicate, keep order
    if not clean_subjects:
        return JsonResponse({'success': False, 'error': 'Please select at least one subject.'}, status=400)

    with transaction.atomic():
        profile.student_category = category
        profile.save()
        # Replace the old selection wholesale with the new one
        RegisteredSubject.objects.filter(student=user).delete()
        RegisteredSubject.objects.bulk_create([
            RegisteredSubject(student=user, category=category, subject_name=name)
            for name in clean_subjects
        ])

    return JsonResponse({
        'success':          True,
        'category':         category,
        'category_display': dict(Profile.STUDENT_CATEGORY_CHOICES).get(category, ''),
        'subjects':         sorted(clean_subjects),
    })


@login_required
@never_cache
@require_GET
def my_subjects(request):
    """
    JSON feed for the "My Subjects" modal (sidebar / home page).
    Returns the signed-in student's passport photo, admission number and other
    key details, plus the subjects they registered. Always the student's own data.
    """
    user    = request.user
    profile = Profile.objects.filter(user=user).first()
    subjects = list(
        RegisteredSubject.objects.filter(student=user)
        .order_by('subject_name')
        .values_list('subject_name', flat=True)
    )

    class_level = ''
    if profile:
        class_level = _normalize_class_level(profile.class_level) or profile.class_level

    return JsonResponse({
        'success':          True,
        'full_name':        user.get_full_name() or user.username,
        'username':         user.username,
        'photo':            profile.get_picture_url if profile else '',
        'admission_number': profile.admission_number if profile else '',
        'class_level':      class_level or '',
        'school':           (profile.school if profile else '') or '',
        'category':         dict(Profile.STUDENT_CATEGORY_CHOICES).get(profile.student_category, '') if profile else '',
        'specialization':   (profile.specialization if profile else '') or '',
        'gender':           (profile.gender if profile else '') or '',
        'subjects':         subjects,
        'count':            len(subjects),
        'profile_url':      reverse('profile', args=[user.username]) + '#performance',
    })


def follow(request, username):
    user    = get_object_or_404(User, username=username)
    profile = Profile.objects.get(user=user)
    if request.user not in profile.follow.all():
        profile.follow.add(request.user)
        profile.save()
        messages.info(request, 'Following')
    else:
        profile.follow.remove(request.user)
        profile.save()
        messages.info(request, 'Unfollow')
    return redirect(request.META.get('HTTP_REFERER'))


def notifications(request):
    return render(request, 'notification.html')


def open_notify(request, pk):
    notification          = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.is_read  = True
    notification.save()
    if notification.class_post:
        return redirect('chat_post_detail', post_id=notification.class_post.post_id)
    return redirect('notifications')


def user_logout(request):
    auth.logout(request)
    return redirect('/')


# ── Live Video Views ──────────────────────────────────────────────

@login_required
def live_room_list(request):
    from django.utils import timezone as tz
    active_sessions   = LiveSession.objects.filter(is_active=True).order_by('-created_at')
    upcoming_sessions = LiveSession.objects.filter(is_active=False, scheduled_at__gt=tz.now()).order_by('scheduled_at')
    return render(request, 'live_room_list.html', {
        'sessions':  active_sessions,
        'upcoming':  upcoming_sessions,
    })


@login_required
def create_live_room(request):
    if request.method == 'POST':
        from django.utils import timezone as tz
        from django.utils.dateparse import parse_datetime

        title       = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        scheduled   = request.POST.get('scheduled_at', '').strip()
        room_name   = str(uuid.uuid4())[:8]
        is_ajax     = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if not title:
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'Please provide a title.'}, status=400)
            messages.error(request, 'Please provide a title for your live session')
            return redirect('live_room_list')

        scheduled_dt = None
        if scheduled:
            scheduled_dt = parse_datetime(scheduled)
            if scheduled_dt and scheduled_dt.tzinfo is None:
                scheduled_dt = tz.make_aware(scheduled_dt)

        is_immediate = scheduled_dt is None or scheduled_dt <= tz.now()

        session = LiveSession.objects.create(
            room_name=room_name,
            title=title,
            description=description,
            created_by=request.user,
            is_active=is_immediate,
            scheduled_at=scheduled_dt if not is_immediate else None,
        )

        if is_ajax:
            if is_immediate:
                return JsonResponse({'ok': True, 'redirect': f'/live/{room_name}/'})
            return JsonResponse({'ok': True, 'scheduled': True,
                                 'title': session.title,
                                 'scheduled_at': scheduled_dt.strftime('%d %b %Y, %I:%M %p')})

        if is_immediate:
            messages.success(request, f'Live room "{title}" created!')
            return redirect('live_room', room_name=room_name)
        messages.success(request, f'"{title}" scheduled for {scheduled_dt.strftime("%d %b %Y, %I:%M %p")}.')
        return redirect('live_room_list')

    return redirect('live_room_list')


@login_required
def live_room(request, room_name):
    session           = get_object_or_404(LiveSession, room_name=room_name, is_active=True)
    participant_count = LiveParticipant.objects.filter(session=session, is_connected=True).count()
    if participant_count >= session.max_participants:
        messages.error(request, 'This room is full. Please try again later.')
        return redirect('live_room_list')
    context = {
        'session':    session,
        'room_name':  room_name,
        'user':       request.user,
        'is_creator': session.created_by == request.user,
        'host_id':    session.created_by.id,
    }
    return render(request, 'live_room.html', context)


@login_required
def end_live_room(request, room_name):
    session           = get_object_or_404(LiveSession, room_name=room_name, created_by=request.user)
    session.is_active = False
    session.save()
    messages.success(request, 'Live session ended successfully')
    return redirect('live_room_list')


@login_required
def start_live_room(request, room_name):
    """Host manually activates a scheduled session."""
    session = get_object_or_404(LiveSession, room_name=room_name, created_by=request.user)
    if not session.is_active:
        session.is_active    = True
        session.scheduled_at = None
        session.save()
        messages.success(request, f'"{session.title}" is now live!')
    return redirect('live_room', room_name=room_name)


# ── CBT Exam Views ───────────────────────────────────────────────

# Test Practice: every student may practise ANY subject, whether or not they registered for it,
# and no subject is compulsory. (Registered subjects are still used elsewhere, e.g. on the profile.)
_CBT_ID_ALIASES = {'maths': 'mathematics'}   # picker id -> CBTExam.subject key


def _cbt_key(picker_id):
    return _CBT_ID_ALIASES.get(picker_id, picker_id)


@login_required
@ensure_csrf_cookie
def cbt_subjects(request):
    """Render the Test Practice subject picker. Students can choose any subjects they like."""
    # Student details printed on the result slip (passport, name, admission no, etc.)
    user = request.user
    profile = Profile.objects.filter(user=user).first()
    student_info = {
        'name': user.get_full_name() or user.username,
        'admission_number': profile.admission_number if profile else '',
        'picture': profile.get_picture_url if profile else '',
        'class_level': (profile.class_level if profile else '') or '',
        'gender': (profile.gender if profile else '') or '',
        'school': (profile.school if profile else '') or '',
        'specialization': (profile.specialization if profile else '') or '',
    }
    return render(request, 'cbt_exams.html', {
        'profile_url': reverse('profile', args=[user.username]),
        'student_info': student_info,
    })


@login_required
def cbt_mathematics(request):
    """Render the practice test page (Mathematics default)."""
    return render(request, 'cbt_mathematics.html')


# kept for backwards compat — alias used by cbt_exam url
cbt_exam = cbt_mathematics


@login_required
def cbt_physics(request):
    """Render the original Physics practice test page (unchanged)."""
    return render(request, 'cbt_physics.html')


@login_required
def cbt_english(request):
    return render(request, 'cbt_english.html')


@login_required
def cbt_chemistry(request):
    return render(request, 'cbt_chemistry.html')


@login_required
def cbt_physics_topics(request):
    return render(request, 'cbt_physics_topics.html')


@login_required
def cbt_mathematics_topics(request):
    return render(request, 'cbt_mathematics_topics.html')


def _grade_for(percentage):
    if percentage >= 70: return 'distinction'
    if percentage >= 60: return 'credit'
    if percentage >= 50: return 'pass'
    if percentage >= 40: return 'nearly'
    return 'fail'


@login_required
@require_POST
def cbt_submit(request):
    """
    Save CBT results for the signed-in student.

    Accepts either
      {"results": [{"subject", "score", "total"}, ...], "time_used_sec": N}   (multi-subject exam)
    or the legacy single-subject payload
      {"subject", "score", "total", "percentage", "grade", "time_used_sec"}.
    Percentage and grade are always recomputed on the server.
    """
    try:
        data = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    valid_subjects = {k for k, _ in CBTExam.SUBJECT_CHOICES}

    items = data.get('results')
    if not isinstance(items, list):
        items = [data]

    try:
        time_used = max(0, int(data.get('time_used_sec', 0)))
    except (TypeError, ValueError):
        time_used = 0

    rows = []
    for it in items[:10]:
        try:
            subject = str(it.get('subject', '')).strip().lower()
            subject = _cbt_key(subject)
            total = int(it.get('total', 0))
            score = int(it.get('score', 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if subject not in valid_subjects or total < 1 or total > 500:
            continue
        score = max(0, min(score, total))
        pct = round(score / total * 100)
        rows.append(CBTExam(student=request.user, subject=subject, score=score, total=total,
                            percentage=pct, grade=_grade_for(pct), time_used_sec=time_used))

    if not rows:
        return JsonResponse({'ok': False, 'error': 'No valid results to save.'}, status=400)

    CBTExam.objects.bulk_create(rows)

    cbt_score, _ = CBTScore.objects.get_or_create(user=request.user)
    cbt_score.recalculate()

    return JsonResponse({
        'ok':     True,
        'saved':  len(rows),
        'points': cbt_score.points,
        'best':   cbt_score.best_score,
    })


# ── CBT Examination (real, timed test on the student's registered subjects) ──
#
# "Test Practice" (cbt_exams.html) is graded in the browser and open to any subject.
# The CBT Examination is the real thing:
#   * a student can only sit the subjects they registered (RegisteredSubject);
#   * questions are drawn on the server from the ExamQuestion bank, in random order,
#     with the options shuffled, and frozen into an ExaminationAttempt;
#   * the correct answers never reach the browser until the paper is submitted;
#   * the countdown is enforced by the server clock (it survives a refresh, and a paper
#     whose time is up is submitted automatically);
#   * on submission each subject's score is saved as a CBTExam row, so the profile,
#     the printed result slip and the admin dashboard pick it up automatically.

EXAM_QUESTIONS_PER_SUBJECT = 40    # drawn at random from the 50-question bank of each subject
EXAM_MINUTES_PER_SUBJECT   = 30    # total time = minutes x number of subjects
EXAM_MAX_SUBJECTS          = 9     # WAEC / NECO allow at most 9 subjects per sitting
EXAM_SAVE_GRACE_SEC        = 20    # answers still accepted this long after the clock hits zero

# RegisteredSubject.subject_name -> CBTExam.subject key
_EXAM_SUBJECT_KEYS = {label: key for key, label in CBTExam.SUBJECT_CHOICES}
_EXAM_SUBJECT_KEYS['Food and Nutrition'] = 'homeeconomics'   # no separate CBTExam choice exists


def _exam_registered_subjects(user):
    return list(
        RegisteredSubject.objects.filter(student=user)
        .order_by('subject_name').values_list('subject_name', flat=True)
    )


def _exam_bank_counts():
    """{subject_name: number of active questions}"""
    return dict(
        ExamQuestion.objects.filter(is_active=True)
        .values_list('subject_name').annotate(n=Count('id'))
    )


def _draw_questions(name):
    """
    Pick up to EXAM_QUESTIONS_PER_SUBJECT questions for one subject.
    Questions that share a passage / diagram are treated as one block: they are all given, together
    and in their set order, or not at all. Blocks and single questions are mixed in random order.
    """
    pool = list(ExamQuestion.objects.filter(subject_name=name, is_active=True).select_related('passage'))
    groups = {}
    units = []
    for q in pool:
        if q.passage_id:
            groups.setdefault(q.passage_id, []).append(q)
        else:
            units.append([q])
    for g in groups.values():
        units.append(sorted(g, key=lambda q: (q.order, q.id)))
    random.shuffle(units)

    chosen, count = [], 0
    for u in units:
        if count + len(u) <= EXAM_QUESTIONS_PER_SUBJECT:
            chosen.append(u)
            count += len(u)
    random.shuffle(chosen)
    return [q for u in chosen for q in u]


def _build_exam_paper(subject_names):
    """Draw and freeze the questions for a paper. Options are shuffled; keys stay A-D."""
    paper = []
    for name in subject_names:
        for q in _draw_questions(name):
            opts = [['A', q.option_a], ['B', q.option_b], ['C', q.option_c], ['D', q.option_d]]
            random.shuffle(opts)
            p = q.passage
            paper.append({
                'id': q.id, 'subject': name, 'text': q.text, 'options': opts,
                'answer': q.answer, 'explanation': q.explanation,
                'image': q.image_url,
                'passage': ({'id': p.id, 'title': p.title, 'text': p.text, 'image': p.image_url} if p else None),
            })
    return paper


def _exam_student(user):
    profile = Profile.objects.filter(user=user).first()
    return {
        'name': user.get_full_name() or user.username,
        'admission_number': profile.admission_number if profile else '',
        'class_level': (profile.class_level if profile else '') or '',
        'picture': profile.get_picture_url if profile else '',
    }


def _finalize_examination(attempt, auto=False):
    """
    Grade a paper on the server and save the results. Idempotent.
    Call inside transaction.atomic() with the attempt row locked (select_for_update).
    """
    if attempt.is_submitted:
        return attempt

    now = timezone.now()
    per = {name: {'score': 0, 'total': 0, 'answered': 0} for name in attempt.subjects}
    for q in attempt.paper:
        row = per.setdefault(q['subject'], {'score': 0, 'total': 0, 'answered': 0})
        row['total'] += 1
        picked = attempt.answers.get(str(q['id']))
        if picked:
            row['answered'] += 1
            if picked == q['answer']:
                row['score'] += 1

    elapsed = attempt.duration_sec if auto else int(
        min(attempt.duration_sec, max(0, (now - attempt.started_at).total_seconds())))

    rows = []
    for name, r in per.items():
        if r['total'] < 1:
            continue
        r['percentage'] = round(r['score'] / r['total'] * 100)
        r['grade'] = _grade_for(r['percentage'])
        key = _EXAM_SUBJECT_KEYS.get(name)
        if key:
            rows.append(CBTExam(student_id=attempt.student_id, subject=key, score=r['score'], total=r['total'],
                                percentage=r['percentage'], grade=r['grade'], time_used_sec=elapsed,
                                exam_type='examination'))
    if rows:
        CBTExam.objects.bulk_create(rows)

    total = sum(r['total'] for r in per.values())
    score = sum(r['score'] for r in per.values())
    attempt.score, attempt.total = score, total
    attempt.percentage = round(score / total * 100) if total else 0
    attempt.grade = _grade_for(attempt.percentage)
    attempt.results = per
    attempt.is_submitted = True
    attempt.auto_submitted = bool(auto)
    attempt.submitted_at = now
    attempt.save()

    cbt_score, _ = CBTScore.objects.get_or_create(user_id=attempt.student_id)
    cbt_score.recalculate()
    return attempt


def _expire_if_due(attempt):
    """Auto-submit an attempt whose time has run out. Returns the (possibly refreshed) attempt."""
    if attempt.is_submitted or attempt.remaining_sec() > 0:
        return attempt
    with transaction.atomic():
        locked = ExaminationAttempt.objects.select_for_update().get(pk=attempt.pk)
        _finalize_examination(locked, auto=True)
    return ExaminationAttempt.objects.get(pk=attempt.pk)


def _close_expired_examinations(user):
    for att in ExaminationAttempt.objects.filter(student=user, is_submitted=False):
        _expire_if_due(att)


def _clean_exam_answers(attempt, incoming):
    """Keep only answers that belong to this paper and use one of the offered option keys."""
    valid = {str(q['id']): {k for k, _ in q['options']} for q in attempt.paper}
    return {
        str(qid): key for qid, key in (incoming or {}).items()
        if str(qid) in valid and isinstance(key, str) and key in valid[str(qid)]
    }


@login_required
@never_cache
@ensure_csrf_cookie
def cbt_examination(request):
    """Landing page: the student's registered subjects, rules, a resume card and past sittings."""
    user = request.user
    _close_expired_examinations(user)

    counts = _exam_bank_counts()
    subjects = [
        {'name': n, 'available': counts.get(n, 0), 'questions': min(counts.get(n, 0), EXAM_QUESTIONS_PER_SUBJECT)}
        for n in _exam_registered_subjects(user)
    ]
    return render(request, 'cbt_examination.html', {
        'view_mode': 'home',
        'subjects': subjects,
        'active': ExaminationAttempt.objects.filter(student=user, is_submitted=False).first(),
        'history': ExaminationAttempt.objects.filter(student=user, is_submitted=True)[:10],
        'max_subjects': EXAM_MAX_SUBJECTS,
        'per_subject': EXAM_QUESTIONS_PER_SUBJECT,
        'minutes_per_subject': EXAM_MINUTES_PER_SUBJECT,
        'is_teacher': _is_teacher(user),
        'profile_url': reverse('profile', args=[user.username]),
    })


@login_required
@require_POST
def cbt_examination_start(request):
    """Create the paper for the ticked subjects and open the examination hall."""
    user = request.user

    def back(msg):
        messages.error(request, msg)
        return redirect('cbt_examination')

    registered = _exam_registered_subjects(user)
    chosen = list(dict.fromkeys(s.strip() for s in request.POST.getlist('subjects') if s.strip()))

    if not registered:
        return back('You have not registered any subjects yet. Register your subjects first, then come back.')
    if not chosen:
        return back('Please select at least one subject.')
    if any(s not in registered for s in chosen):
        return back('You can only sit the examination for subjects you registered.')
    if len(chosen) > EXAM_MAX_SUBJECTS:
        return back(f'You can sit at most {EXAM_MAX_SUBJECTS} subjects at a time.')

    counts = _exam_bank_counts()
    empty = [s for s in chosen if counts.get(s, 0) < 1]
    if empty:
        return back('No questions are available yet for: ' + ', '.join(empty) + '. Please untick it and try again.')

    with transaction.atomic():
        _close_expired_examinations(user)
        existing = (ExaminationAttempt.objects.select_for_update()
                    .filter(student=user, is_submitted=False).first())
        if existing:
            return redirect('cbt_examination_take', attempt_id=existing.attempt_id)

        ordered = [s for s in registered if s in chosen]
        attempt = ExaminationAttempt.objects.create(
            student=user, subjects=ordered, paper=_build_exam_paper(ordered), answers={},
            duration_sec=len(ordered) * EXAM_MINUTES_PER_SUBJECT * 60,
        )
    return redirect('cbt_examination_take', attempt_id=attempt.attempt_id)


@login_required
@never_cache
@ensure_csrf_cookie
def cbt_examination_take(request, attempt_id):
    """The examination hall. Questions are sent WITHOUT the correct answers."""
    attempt = get_object_or_404(ExaminationAttempt, attempt_id=attempt_id, student=request.user)
    attempt = _expire_if_due(attempt)
    if attempt.is_submitted:
        return redirect('cbt_examination_result', attempt_id=attempt.attempt_id)

    payload = {
        'remaining': max(0, attempt.remaining_sec()),
        'subjects': attempt.subjects,
        'questions': [
            {'id': q['id'], 'subject': q['subject'], 'text': q['text'], 'options': q['options'],
             'image': q.get('image', ''), 'passage': q.get('passage')}
            for q in attempt.paper
        ],
        'answers': attempt.answers,
        'saveUrl': reverse('cbt_examination_save', args=[attempt.attempt_id]),
        'submitUrl': reverse('cbt_examination_submit', args=[attempt.attempt_id]),
        'resultUrl': reverse('cbt_examination_result', args=[attempt.attempt_id]),
    }
    return render(request, 'cbt_examination.html', {
        'view_mode': 'exam',
        'exam_payload': payload,
        'student_info': _exam_student(request.user),
        'profile_url': reverse('profile', args=[request.user.username]),
    })


@login_required
@require_POST
def cbt_examination_save(request, attempt_id):
    """Autosave the student's answers (full map, last write wins)."""
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    if not isinstance(data.get('answers'), dict):
        return JsonResponse({'ok': False, 'error': 'answers must be an object'}, status=400)

    with transaction.atomic():
        attempt = get_object_or_404(ExaminationAttempt.objects.select_for_update(),
                                    attempt_id=attempt_id, student=request.user)
        result_url = reverse('cbt_examination_result', args=[attempt.attempt_id])
        if attempt.is_submitted:
            return JsonResponse({'ok': False, 'submitted': True, 'result_url': result_url})

        remaining = attempt.remaining_sec()
        if remaining <= -EXAM_SAVE_GRACE_SEC:
            _finalize_examination(attempt, auto=True)
            return JsonResponse({'ok': False, 'expired': True, 'result_url': result_url})

        attempt.answers = _clean_exam_answers(attempt, data['answers'])
        attempt.save(update_fields=['answers'])
    return JsonResponse({'ok': True, 'remaining': max(0, remaining), 'answered': len(attempt.answers)})


@login_required
@require_POST
def cbt_examination_submit(request, attempt_id):
    """Final submit (button or timer). Grades on the server and saves the results."""
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        data = {}

    with transaction.atomic():
        attempt = get_object_or_404(ExaminationAttempt.objects.select_for_update(),
                                    attempt_id=attempt_id, student=request.user)
        if not attempt.is_submitted:
            remaining = attempt.remaining_sec()
            if isinstance(data.get('answers'), dict) and remaining > -EXAM_SAVE_GRACE_SEC:
                attempt.answers = _clean_exam_answers(attempt, data['answers'])
            _finalize_examination(attempt, auto=remaining <= 0)

    return JsonResponse({
        'ok': True,
        'result_url': reverse('cbt_examination_result', args=[attempt.attempt_id]),
    })


@login_required
@never_cache
def cbt_examination_result(request, attempt_id):
    """Result slip + answer review (correct answers are only revealed from here)."""
    attempt = get_object_or_404(ExaminationAttempt, attempt_id=attempt_id, student=request.user)
    attempt = _expire_if_due(attempt)
    if not attempt.is_submitted:
        return redirect('cbt_examination_take', attempt_id=attempt.attempt_id)

    grade_labels = dict(CBTExam.GRADE_CHOICES)
    sections = []
    for name in attempt.subjects:
        r = attempt.results.get(name)
        if not r:
            continue
        questions, last_passage = [], None
        for n, q in enumerate([q for q in attempt.paper if q['subject'] == name], start=1):
            picked = attempt.answers.get(str(q['id']))
            options = [
                {'label': 'ABCD'[i], 'text': text, 'is_correct': key == q['answer'], 'is_picked': key == picked}
                for i, (key, text) in enumerate(q['options'])
            ]
            p = q.get('passage')
            pid = p['id'] if p else None
            questions.append({
                'n': n, 'text': q['text'], 'options': options, 'explanation': q.get('explanation', ''),
                'image': q.get('image', ''), 'passage': p if pid != last_passage else None,
                'status': 'skipped' if not picked else ('correct' if picked == q['answer'] else 'wrong'),
            })
            last_passage = pid
        sections.append({
            'name': name, 'score': r['score'], 'total': r['total'], 'answered': r['answered'],
            'percentage': r['percentage'], 'grade': r['grade'], 'grade_label': grade_labels.get(r['grade'], ''),
            'questions': questions,
        })

    return render(request, 'cbt_examination.html', {
        'view_mode': 'result',
        'attempt': attempt,
        'print_date': _as_local(attempt.submitted_at).strftime('%Y-%m-%d'),
        'sections': sections,
        'grade_label': grade_labels.get(attempt.grade, ''),
        'remark': GRADE_REMARKS.get(attempt.grade, ''),
        'student_info': _exam_student(request.user),
        'profile_url': reverse('profile', args=[request.user.username]),
    })


# ── My Results: every test result grouped by test date, with a Print button per date ──
# (Printing itself is print_test_results, which re-queries the database for that date.)

@login_required
@never_cache
@require_GET
def my_results(request, username=None):
    """A student's own results (staff may open any student's via /results/<username>/)."""
    target = request.user if username is None else get_object_or_404(User, username=username)
    if not _can_view_private(request, target):
        raise PermissionDenied

    labels = dict(CBTExam.SUBJECT_CHOICES)
    exams = list(CBTExam.objects.filter(student=target).order_by('-taken_at')[:EXAM_HISTORY_LIMIT])

    days = []   # newest first, so each calendar day's rows are contiguous
    for ex in exams:
        local = _as_local(ex.taken_at)
        iso = local.strftime('%Y-%m-%d')
        if not days or days[-1]['date_iso'] != iso:
            days.append({'date_iso': iso, 'date': local, 'rows': []})
        days[-1]['rows'].append({
            'subject': labels.get(ex.subject, ex.subject.replace('_', ' ').title()),
            'type': ex.get_exam_type_display(),
            'is_exam': ex.exam_type == 'examination',
            'score': ex.score, 'total': ex.total, 'percentage': ex.percentage,
            'grade': ex.grade, 'grade_display': ex.get_grade_display(),
        })

    grade_labels = dict(CBTExam.GRADE_CHOICES)
    for d in days:
        got = sum(r['score'] for r in d['rows'])
        out_of = sum(r['total'] for r in d['rows'])
        d['got'], d['out_of'] = got, out_of
        d['pct'] = round(got / out_of * 100) if out_of else 0
        d['grade_display'] = grade_labels.get(_grade_for(d['pct']), '')
        d['subject_count'] = len({r['subject'] for r in d['rows']})

    return render(request, 'my_results.html', {
        'target': target,
        'is_owner': target == request.user,
        'days': days,
        'result_count': len(exams),
        'limit': EXAM_HISTORY_LIMIT,
        'profile_url': reverse('profile', args=[request.user.username]),
    })


# ── CBT Examination: question bank management (teachers / staff only) ──
#
# Teachers upload questions here (CSV, Excel or pasted text), attach pictures / diagrams to questions,
# and create "passages" (shared information: a reading text, table, map or diagram) that several
# questions refer to. Parsing/validation lives in school/exam_import.py.

EXAM_BANK_PAGE_SIZE = 20
EXAM_UPLOAD_MAX_BYTES = 2 * 1024 * 1024


def _exam_teacher_only(view):
    """Staff / superusers only (the project's existing teacher role)."""
    @wraps(view)
    @login_required
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not _is_teacher(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _bank_redirect(request, subject=''):
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    url = reverse('cbt_question_bank')
    return redirect(url + ('?' + urlencode({'subject': subject}) if subject else ''))


def _bank_fail(request, msg, subject=''):
    messages.error(request, msg)
    return _bank_redirect(request, subject)


@_exam_teacher_only
@ensure_csrf_cookie
def cbt_question_bank(request):
    """Overview of the bank per subject, upload / add forms, passages and a searchable list of questions."""
    subject = request.GET.get('subject', '').strip()
    search = request.GET.get('q', '').strip()

    rows = {r['subject_name']: r for r in ExamQuestion.objects.values('subject_name').annotate(
        total=Count('id'), active=Count('id', filter=Q(is_active=True)))}
    stats = [{'name': n, 'active': rows.get(n, {}).get('active', 0), 'total': rows.get(n, {}).get('total', 0)}
             for n in sorted(SENIOR_SECONDARY_SUBJECTS)]

    qs = ExamQuestion.objects.select_related('passage')
    if subject:
        qs = qs.filter(subject_name=subject)
    if search:
        qs = qs.filter(text__icontains=search)
    page = Paginator(qs, EXAM_BANK_PAGE_SIZE).get_page(request.GET.get('page'))

    all_passages = list(ExamPassage.objects.annotate(n=Count('questions')).order_by('subject_name', 'id')[:500])
    shown_passages = [p for p in all_passages if not subject or p.subject_name == subject]

    return render(request, 'cbt_question_bank.html', {
        'stats': stats,
        'subject_names': sorted(SENIOR_SECONDARY_SUBJECTS),
        'page_obj': page,
        'subject': subject,
        'search': search,
        'query': urlencode({k: v for k, v in (('subject', subject), ('q', search)) if v}),
        'per_subject': EXAM_QUESTIONS_PER_SUBJECT,
        'total_active': sum(r['active'] for r in stats),
        'passages': shown_passages,
        'passage_options': all_passages,
        'profile_url': reverse('profile', args=[request.user.username]),
    })


@_exam_teacher_only
@require_POST
def cbt_question_upload(request):
    """Import questions from an uploaded CSV / Excel / text file (plus optional pictures), or pasted text."""
    default_subject = request.POST.get('subject', '').strip()
    replace = request.POST.get('replace') == 'on'
    upload = request.FILES.get('file')
    pasted = request.POST.get('pasted', '').strip()

    if default_subject and default_subject not in SENIOR_SECONDARY_SUBJECTS:
        return _bank_fail(request, 'Please choose a valid subject.')
    if not upload and not pasted:
        return _bank_fail(request, 'Choose a file to upload or paste your questions.', default_subject)

    try:
        if upload:
            if upload.size > EXAM_UPLOAD_MAX_BYTES:
                return _bank_fail(request, 'That file is too large (limit 2 MB).', default_subject)
            rows = rows_from_upload(upload.name, upload.read())
        else:
            rows = rows_from_text(pasted)
    except ValueError as e:
        return _bank_fail(request, str(e), default_subject)

    if len(rows) > EXAM_IMPORT_MAX_ROWS + 1:
        return _bank_fail(request, f'Too many rows. Please upload at most {EXAM_IMPORT_MAX_ROWS} questions at a time.', default_subject)

    blobs, image_errors = image_blobs_from_uploads(request.FILES.getlist('images'))
    questions, errors = parse_rows(rows, SENIOR_SECONDARY_SUBJECTS, default_subject, images=blobs)
    problems = [(0, e) for e in image_errors] + errors

    if replace and problems:
        # Never wipe a subject's existing questions on the strength of a file that has mistakes.
        messages.error(request, 'Nothing was changed: "replace" needs a file without errors. Fix the problems below and upload again.')
    elif questions:
        created, present, removed, import_errors = import_questions(questions, replace=replace)
        if replace and import_errors:
            messages.error(request, 'Nothing was changed: "replace" needs a file without errors. Fix the problems below and upload again.')
            problems += import_errors
        else:
            problems += import_errors
            bits = [f'{created} new question{"s" if created != 1 else ""} added']
            if present:
                bits.append(f'{present} already in the bank')
            if removed:
                bits.append(f'{removed} old question{"s" if removed != 1 else ""} replaced')
            messages.success(request, 'Upload complete: ' + ', '.join(bits) + '.')
    elif not problems:
        messages.error(request, 'No questions were found in that file.')

    if problems:
        problems.sort(key=lambda x: x[0])
        messages.warning(request, f'{len(problems)} problem{"s" if len(problems) != 1 else ""} found:')
        for line, msg in problems[:8]:
            messages.warning(request, (f'Line {line}: ' if line else '') + msg)
        if len(problems) > 8:
            messages.warning(request, f'…and {len(problems) - 8} more.')
    return _bank_redirect(request, default_subject)


def _question_from_post(request):
    p = request.POST
    return clean_question(p.get('subject', ''), p.get('text', ''), p.get('option_a', ''), p.get('option_b', ''),
                          p.get('option_c', ''), p.get('option_d', ''), p.get('answer', ''),
                          p.get('explanation', ''), SENIOR_SECONDARY_SUBJECTS)


def _read_image_upload(request, field='image'):
    """(file, error) for an optional picture in a form. The file is rewound so it can still be saved."""
    f = request.FILES.get(field)
    if not f:
        return None, None
    err = check_image(f.name, f.read())
    f.seek(0)
    return (None, err) if err else (f, None)


def _save_question_from_post(request, obj):
    """Validate the add/edit form and save `obj`. Returns an error message, or None on success."""
    data, err = _question_from_post(request)
    if err:
        return err
    p = request.POST

    passage = None
    pid = p.get('passage', '').strip()
    if pid:
        passage = ExamPassage.objects.filter(pk=int(pid) if pid.isdigit() else 0).first()
        if not passage:
            return 'the chosen passage no longer exists'
        if passage.subject_name != data['subject_name']:
            return 'the chosen passage belongs to a different subject'
    try:
        order = max(0, min(int(p.get('order') or 0), 999))
    except ValueError:
        return 'the position in the passage must be a number'
    picture, err = _read_image_upload(request)
    if err:
        return err

    obj.subject_name, obj.text = data['subject_name'], data['text']
    obj.option_a, obj.option_b, obj.option_c, obj.option_d = data['a'], data['b'], data['c'], data['d']
    obj.answer, obj.explanation = data['answer'], data['explanation']
    obj.passage, obj.order = passage, (order if passage else 0)
    if picture:
        obj.image = picture
    elif p.get('remove_image') == 'on':
        obj.image = None
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError:
        return 'an identical question already exists'
    return None


@_exam_teacher_only
@require_POST
def cbt_question_add(request):
    err = _save_question_from_post(request, ExamQuestion())
    if err:
        return _bank_fail(request, 'Question not saved: ' + err + '.', request.POST.get('subject', ''))
    messages.success(request, 'Question added.')
    return _bank_redirect(request, request.POST.get('subject', ''))


@_exam_teacher_only
@require_POST
def cbt_question_edit(request, pk):
    err = _save_question_from_post(request, get_object_or_404(ExamQuestion, pk=pk))
    if err:
        return _bank_fail(request, 'Changes not saved: ' + err + '.')
    messages.success(request, 'Question updated.')
    return _bank_redirect(request)


@_exam_teacher_only
@require_POST
def cbt_question_toggle(request, pk):
    obj = get_object_or_404(ExamQuestion, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    messages.success(request, 'Question is now ' + ('active.' if obj.is_active else 'switched off (students will not get it).'))
    return _bank_redirect(request)


@_exam_teacher_only
@require_POST
def cbt_question_delete(request, pk):
    get_object_or_404(ExamQuestion, pk=pk).delete()
    messages.success(request, 'Question deleted.')
    return _bank_redirect(request)


@_exam_teacher_only
@require_POST
def cbt_passage_save(request, pk=None):
    """Create (no pk) or edit a passage: shared text and/or picture for a group of questions."""
    obj = get_object_or_404(ExamPassage, pk=pk) if pk else ExamPassage()
    p = request.POST
    subject = p.get('subject', '').strip()
    title, text, ref = p.get('title', '').strip(), p.get('text', '').strip(), p.get('ref', '').strip()

    def fail(msg):
        return _bank_fail(request, 'Passage not saved: ' + msg + '.', subject)

    if subject not in SENIOR_SECONDARY_SUBJECTS:
        return fail('please choose a subject')
    if pk and obj.subject_name != subject and obj.questions.exists():
        return fail('its questions belong to ' + obj.subject_name + ', so the subject cannot be changed')
    if len(title) > 150 or len(ref) > 60:
        return fail('the title or reference is too long')
    picture, err = _read_image_upload(request)
    if err:
        return fail(err)
    keeps_image = bool(obj.image) and p.get('remove_image') != 'on'
    if not text and not picture and not keeps_image:
        return fail('give it some text or a picture')
    if ref and ExamPassage.objects.filter(subject_name=subject, ref__iexact=ref).exclude(pk=obj.pk).exists():
        return fail(f'another passage in {subject} already uses the reference "{ref}"')

    obj.subject_name, obj.title, obj.text, obj.ref = subject, title, text, ref
    if picture:
        obj.image = picture
    elif p.get('remove_image') == 'on':
        obj.image = None
    obj.save()
    messages.success(request, 'Passage saved. You can now attach questions to it with "Add a question" or "Edit".')
    return _bank_redirect(request, subject)


@_exam_teacher_only
@require_POST
def cbt_passage_delete(request, pk):
    obj = get_object_or_404(ExamPassage, pk=pk)
    n = obj.questions.count()
    obj.delete()      # its questions are kept; they simply stop sharing a passage
    messages.success(request, 'Passage deleted.' + (f' Its {n} question{"s" if n != 1 else ""} {"were" if n != 1 else "was"} kept without a passage.' if n else ''))
    return _bank_redirect(request)


@_exam_teacher_only
@require_GET
def cbt_question_template(request):
    """A ready-to-fill CSV template (opens in Excel / Google Sheets)."""
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="exam_questions_template.csv"'
    resp.write('\ufeff')   # so Excel reads UTF-8 correctly
    w = csv.writer(resp)
    w.writerow(['subject', 'question', 'option_a', 'option_b', 'option_c', 'option_d', 'answer', 'explanation',
                'image', 'passage', 'passage_title', 'passage_text', 'passage_image'])
    w.writerow(['Mathematics', 'Solve for x: 3x - 7 = 11', '6', '4', '5', '18', 'A', '3x = 18, so x = 6', '', '', '', '', ''])
    w.writerow(['Physics', 'The diagram shows a simple circuit. What is the reading of the ammeter?', '1 A', '2 A', '3 A', '6 A', 'B', '', 'circuit1.png', '', '', '', ''])
    w.writerow(['Geography', 'Which feature is marked X on the map?', 'A delta', 'A lagoon', 'A plateau', 'A spit', 'A', '', '', 'MAP1', 'Map of a coastline', '', 'coast_map.png'])
    w.writerow(['Geography', 'Which of the places marked on the map is a port?', 'P', 'Q', 'R', 'S', 'C', '', '', 'MAP1', '', '', ''])
    w.writerow(['English Language', 'According to the passage, why did Ade return home?', 'He was sick', 'He was tired', 'He lost his job', 'He missed his family', 'D', '', '', 'P1', 'Comprehension passage', 'Write or paste the whole passage here...', ''])
    w.writerow(['English Language', 'The word "reluctantly" in the passage means', 'willingly', 'unwillingly', 'quickly', 'happily', 'B', '', '', 'P1', '', '', ''])
    return resp


# ── Student Q&A Centre (chat room) ──────────────────────────────────
#
# Roles: the project has no separate Teacher model. Staff (is_staff) and
# superusers are the teachers/administrators — the same rule the admin
# dashboard already uses — so `_is_teacher()` follows that.
#
# Subjects and class levels come from the lists already used for student
# registration and profiles (SENIOR_SECONDARY_SUBJECTS / CLASS_LEVEL_CHOICES).

QA_PAGE_SIZE        = 10
QA_ANSWER_PAGE_SIZE = 20
QA_TITLE_MIN, QA_TITLE_MAX = 5, 300
QA_BODY_MIN,  QA_BODY_MAX  = 10, 5000
QA_ANSWER_MAX       = 5000
QA_TOPIC_MAX        = 150
QA_MAX_IMAGE_BYTES  = 5 * 1024 * 1024
QA_MAX_AUDIO_BYTES  = 10 * 1024 * 1024
QA_ALLOWED_AUDIO_TYPES = (
    'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/ogg',
    'audio/mp4', 'audio/x-m4a', 'audio/m4a', 'audio/aac', 'audio/webm',
)
QA_ALLOWED_AUDIO_EXTS = ('.mp3', '.wav', '.ogg', '.oga', '.m4a', '.mp4', '.aac', '.webm')

QA_FORM_LIMITS = {
    'title_min': QA_TITLE_MIN, 'title_max': QA_TITLE_MAX,
    'body_min': QA_BODY_MIN, 'body_max': QA_BODY_MAX, 'topic_max': QA_TOPIC_MAX,
}

QA_STATUS_CHOICES = [
    ('',           'All statuses'),
    ('unanswered', 'Unanswered'),
    ('answered',   'Answered'),
    ('resolved',   'Resolved'),
]
QA_SORT_CHOICES = [
    ('latest',  'Newest'),
    ('active',  'Recently active'),
    ('answers', 'Most answers'),
    ('oldest',  'Oldest'),
]
QA_ANSWER_SORT_CHOICES = [
    ('helpful', 'Most helpful'),
    ('oldest',  'Oldest first'),
    ('newest',  'Newest first'),
]


def _is_teacher(user):
    """Teachers/administrators = staff or superusers (existing role system)."""
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _wants_json(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _display_name(user):
    return user.get_full_name() or user.username


def _qa_fail(request, message, fallback_url, status=400):
    """Error response that suits both AJAX (JSON) and normal form posts (message + redirect)."""
    if _wants_json(request):
        return JsonResponse({'success': False, 'error': message}, status=status)
    messages.error(request, message)
    return redirect(fallback_url)


def _back_or(request, fallback_name, *args):
    """Redirect to the page the user came from (same host only), else a named URL."""
    ref = request.META.get('HTTP_REFERER')
    if ref and url_has_allowed_host_and_scheme(
            ref, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(ref)
    return redirect(fallback_name, *args)


def _qa_subject_names():
    """Canonical subject choices for new questions: registered subject list + General."""
    return sorted(SENIOR_SECONDARY_SUBJECTS) + ['General']


def _qa_filter_subjects():
    """Subjects for the filter bar: the canonical list plus any legacy values already used on old questions."""
    names = _qa_subject_names()
    legacy = (ClassPost.objects.exclude(subject='').exclude(subject__in=names)
              .values_list('subject', flat=True).distinct())
    return names + sorted(set(legacy))


def _default_class_level(user):
    """The student's own class level (SS1/SS2/SS3) from their profile, if known."""
    try:
        level = _normalize_class_level(user.profile.class_level)
    except Exception:
        level = None
    level = (level or '')[:3]
    return level if level in CLASS_LEVEL_CHOICES else ''


def _clean_question_fields(data, allowed_subjects):
    """Server-side validation for the question form. Returns (cleaned, errors)."""
    errors = {}
    title   = ' '.join((data.get('title') or '').split())
    content = (data.get('content') or '').strip()
    subject = (data.get('subject') or '').strip()
    level   = (data.get('class_level') or '').strip().upper()
    topic   = ' '.join((data.get('topic') or '').split())

    if len(title) < QA_TITLE_MIN:
        errors['title'] = f'Enter a clear title (at least {QA_TITLE_MIN} characters).'
    elif len(title) > QA_TITLE_MAX:
        errors['title'] = f'The title is too long (maximum {QA_TITLE_MAX} characters).'

    by_lower = {s.lower(): s for s in allowed_subjects}
    if not subject:
        errors['subject'] = 'Choose a subject.'
    elif subject.lower() not in by_lower:
        errors['subject'] = 'Choose a subject from the list.'
    else:
        subject = by_lower[subject.lower()]

    if level and level not in CLASS_LEVEL_CHOICES:
        errors['class_level'] = 'Choose SS1, SS2 or SS3.'

    if len(topic) > QA_TOPIC_MAX:
        errors['topic'] = f'The topic is too long (maximum {QA_TOPIC_MAX} characters).'

    if len(content) < QA_BODY_MIN:
        errors['content'] = f'Explain your question a little more (at least {QA_BODY_MIN} characters).'
    elif len(content) > QA_BODY_MAX:
        errors['content'] = f'The explanation is too long (maximum {QA_BODY_MAX} characters).'

    cleaned = {'title': title, 'content': content, 'subject': subject,
               'class_level': level, 'topic': topic}
    return cleaned, errors


def _check_image_upload(upload):
    """Return an error string if an uploaded image is unacceptable, else None."""
    if upload.content_type not in ALLOWED_PICTURE_TYPES:
        return 'Image must be a JPG, PNG, WEBP or GIF file.'
    if upload.size > QA_MAX_IMAGE_BYTES:
        return 'Image must be 5 MB or smaller.'
    try:
        from PIL import Image
        Image.open(upload).verify()
        upload.seek(0)
    except ImportError:
        pass
    except Exception:
        return 'That file is not a valid image.'
    return None


def _looks_like_audio(upload):
    """Check the file's first bytes against common audio containers (the content type is client-supplied)."""
    try:
        head = upload.read(16)
        upload.seek(0)
    except Exception:
        return False
    return bool(
        head[:3] == b'ID3'                                   # MP3 with ID3 tag
        or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)   # MP3 / AAC frame sync
        or (head[:4] == b'RIFF' and head[8:12] == b'WAVE')   # WAV
        or head[:4] == b'OggS'                               # Ogg
        or head[:4] == b'\x1a\x45\xdf\xa3'                   # WebM / Matroska
        or head[4:8] == b'ftyp'                              # MP4 / M4A
    )


def _check_audio_upload(upload):
    """Return an error string if an uploaded audio file is unacceptable, else None."""
    name = (upload.name or '').lower()
    if upload.content_type not in QA_ALLOWED_AUDIO_TYPES or not name.endswith(QA_ALLOWED_AUDIO_EXTS):
        return 'Audio must be an MP3, WAV, OGG, M4A or WEBM file.'
    if upload.size > QA_MAX_AUDIO_BYTES:
        return 'Audio must be 10 MB or smaller.'
    if not _looks_like_audio(upload):
        return 'That file is not a valid audio recording.'
    return None


def _notify(user_id, post, message):
    Notification.objects.create(user_id=user_id, class_post=post, message=message)


# ── Question list ───────────────────────────────────────────────────

@login_required
def chat_room(request):
    """Student Q&A Centre: searchable, filterable, paginated list of questions."""
    q       = (request.GET.get('q') or '').strip()[:100]
    subject = (request.GET.get('subject') or '').strip()
    level   = (request.GET.get('level') or '').strip().upper()
    status  = (request.GET.get('status') or '').strip()
    sort    = (request.GET.get('sort') or 'latest').strip()
    scope   = (request.GET.get('scope') or '').strip()      # '' | 'mine' | 'saved'

    # Links created before the redesign used these values.
    if sort == 'unanswered':
        sort, status = 'latest', status or 'unanswered'
    if status == 'unresolved':
        status = 'open'
    if sort not in dict(QA_SORT_CHOICES):
        sort = 'latest'
    if status not in ('open',) + tuple(k for k, _ in QA_STATUS_CHOICES if k):
        status = ''
    if level not in CLASS_LEVEL_CHOICES:
        level = ''
    if scope not in ('mine', 'saved'):
        scope = ''

    posts = ClassPost.objects.select_related('author', 'author__profile')

    if scope == 'mine':
        posts = posts.filter(author=request.user)
    elif scope == 'saved':
        posts = posts.filter(bookmarked_by=request.user)
    if subject:
        posts = posts.filter(subject__iexact=subject)
    if level:
        posts = posts.filter(class_level=level)
    for term in q.split()[:5]:
        posts = posts.filter(
            Q(title__icontains=term) | Q(content__icontains=term) |
            Q(topic__icontains=term) | Q(subject__icontains=term)
        )

    posts = posts.annotate(
        answer_total=Count('class_comments', distinct=True),
        last_activity_at=Coalesce(
            Max('class_comments__created_at'), F('created_at'),
            output_field=DateTimeField(),
        ),
    )

    if status == 'unanswered':
        posts = posts.filter(answer_total=0)
    elif status == 'answered':
        posts = posts.filter(answer_total__gt=0, is_resolved=False)
    elif status == 'resolved':
        posts = posts.filter(is_resolved=True)
    elif status == 'open':
        posts = posts.filter(is_resolved=False)

    order = {
        'latest':  ('-created_at',),
        'oldest':  ('created_at',),
        'active':  ('-last_activity_at', '-created_at'),
        'answers': ('-answer_total', '-created_at'),
    }[sort]
    posts = posts.order_by(*order)

    page_obj = Paginator(posts, QA_PAGE_SIZE).get_page(request.GET.get('page'))

    # One extra query: which of these questions has the user saved?
    page_ids = [p.pk for p in page_obj.object_list]
    saved_ids = set(
        ClassPost.objects.filter(pk__in=page_ids, bookmarked_by=request.user).values_list('pk', flat=True)
    )
    for p in page_obj.object_list:
        p.is_saved = p.pk in saved_ids

    params = {k: v for k, v in (('q', q), ('subject', subject), ('level', level),
                                ('status', status if status != 'open' else ''),
                                ('sort', sort if sort != 'latest' else ''), ('scope', scope)) if v}
    context = {
        'page_obj':        page_obj,
        'total_found':     page_obj.paginator.count,
        'q':               q,
        'current_subject': subject,
        'current_level':   level,
        'current_status':  status,
        'current_sort':    sort,
        'current_scope':   scope,
        'subject_options': _qa_filter_subjects(),
        'level_options':   CLASS_LEVEL_CHOICES,
        'form_subjects':   _qa_subject_names(),          # ask-a-question modal
        'default_level':   _default_class_level(request.user),
        'limits':          QA_FORM_LIMITS,
        'status_options':  QA_STATUS_CHOICES,
        'sort_options':    QA_SORT_CHOICES,
        'has_filters':     bool(q or subject or level or status),
        'querystring':     urlencode(params),   # used by pagination links
    }
    return render(request, 'chat_room.html', context)


# ── Ask / edit / delete a question ──────────────────────────────────

@login_required
def create_class_post(request):
    """Save a new question. The form is the modal on the Q&A Centre page."""
    if request.method != 'POST':
        return redirect('chat_room')

    cleaned, errors = _clean_question_fields(request.POST, _qa_subject_names())
    image = request.FILES.get('image')
    audio = request.FILES.get('audio')
    if image:
        err = _check_image_upload(image)
        if err:
            errors['image'] = err
    if audio:
        err = _check_audio_upload(audio)
        if err:
            errors['audio'] = err

    if errors:
        if _wants_json(request):
            return JsonResponse({'success': False, 'errors': errors}, status=400)
        messages.error(request, ' '.join(errors.values()))
        return redirect('chat_room')

    post = ClassPost.objects.create(
        author=request.user, image=image or None, audio=audio or None, **cleaned
    )
    messages.success(request, 'Your question has been posted.')

    url = reverse('chat_post_detail', args=[post.post_id])
    if _wants_json(request):
        return JsonResponse({'success': True, 'post_id': str(post.post_id), 'redirect': url})
    return redirect(url)


@login_required
def edit_class_post(request, post_id):
    """Save edits to your own question (the form is the Edit modal on the question page)."""
    post = get_object_or_404(ClassPost, post_id=post_id)
    if post.author_id != request.user.pk:
        raise PermissionDenied
    detail_url = reverse('chat_post_detail', args=[post.post_id])
    if request.method != 'POST':
        return redirect(detail_url)

    allowed = _qa_subject_names() + ([post.subject] if post.subject else [])
    cleaned, errors = _clean_question_fields(request.POST, allowed)
    image = request.FILES.get('image')
    audio = request.FILES.get('audio')
    if image:
        err = _check_image_upload(image)
        if err:
            errors['image'] = err
    if audio:
        err = _check_audio_upload(audio)
        if err:
            errors['audio'] = err
    if errors:
        if _wants_json(request):
            return JsonResponse({'success': False, 'errors': errors}, status=400)
        messages.error(request, ' '.join(errors.values()))
        return redirect(detail_url)

    for field, value in cleaned.items():
        setattr(post, field, value)
    if image:
        post.image = image
    elif request.POST.get('remove_image'):
        post.image = None
    if audio:
        post.audio = audio
    elif request.POST.get('remove_audio'):
        post.audio = None
    post.save()

    messages.success(request, 'Your question has been updated.')
    if _wants_json(request):
        return JsonResponse({'success': True, 'redirect': detail_url})
    return redirect(detail_url)


@login_required
@require_POST
def delete_class_post(request, post_id):
    """Delete a question: its author, or a teacher/admin moderating it."""
    post = get_object_or_404(ClassPost, post_id=post_id)
    if not (post.author_id == request.user.pk or _is_teacher(request.user)):
        raise PermissionDenied
    post.delete()
    messages.success(request, 'The question has been deleted.')
    return redirect('chat_room')


# ── Question page ───────────────────────────────────────────────────

@login_required
def chat_post_detail(request, post_id):
    """A single question with its accepted answer and paginated answers."""
    post = get_object_or_404(
        ClassPost.objects.select_related('author', 'author__profile'), post_id=post_id
    )
    user    = request.user
    teacher = _is_teacher(user)

    # Count the view without touching updated_at.
    ClassPost.objects.filter(pk=post.pk).update(view=F('view') + 1)

    sort = request.GET.get('sort', 'helpful')
    if sort not in dict(QA_ANSWER_SORT_CHOICES):
        sort = 'helpful'

    base = (ClassPostComment.objects.filter(post=post)
            .select_related('commentator', 'commentator__profile')
            .annotate(helpful_total=Count('helpful', distinct=True),
                      like_total=Count('like', distinct=True)))

    accepted = base.filter(pk=post.accepted_answer_id).first() if post.accepted_answer_id else None
    others = base.exclude(pk=accepted.pk) if accepted else base
    others = others.order_by(*{
        'helpful': ('-helpful_total', 'created_at'),
        'oldest':  ('created_at',),
        'newest':  ('-created_at',),
    }[sort])

    page_obj = Paginator(others, QA_ANSWER_PAGE_SIZE).get_page(request.GET.get('page'))
    visible = ([accepted] if accepted else []) + list(page_obj.object_list)
    ids = [c.pk for c in visible]

    # Per-user state for the visible answers, in a handful of queries.
    liked    = set(ClassPostComment.objects.filter(pk__in=ids, like=user).values_list('pk', flat=True))
    helpful  = set(ClassPostComment.objects.filter(pk__in=ids, helpful=user).values_list('pk', flat=True))
    reported = set(ClassPostReport.objects.filter(reporter=user, comment_id__in=ids)
                   .values_list('comment_id', flat=True))

    is_asker   = post.author_id == user.pk
    can_accept = is_asker or teacher

    # Open reports are only shown to teachers/admins.
    post_reports, comment_reports = [], {}
    if teacher:
        open_reports = (ClassPostReport.objects.filter(is_handled=False)
                        .filter(Q(post=post) | Q(comment_id__in=ids))
                        .select_related('reporter'))
        for r in open_reports:
            if r.post_id:
                post_reports.append(r)
            else:
                comment_reports.setdefault(r.comment_id, []).append(r)

    for c in visible:
        c.user_liked    = c.pk in liked
        c.user_helpful  = c.pk in helpful
        c.user_reported = c.pk in reported
        c.is_mine       = c.commentator_id == user.pk
        c.can_edit      = c.is_mine
        c.can_delete    = c.is_mine or teacher
        c.can_vote      = not c.is_mine
        c.can_report    = not c.is_mine
        c.can_accept    = can_accept and c.commentator_id != post.author_id
        c.is_accepted   = c.pk == post.accepted_answer_id
        c.open_reports  = comment_reports.get(c.pk, [])   # (not `reports`: that is the reverse relation name)

    context = {
        'post':             post,
        'accepted':         accepted,
        'answers':          visible,          # accepted answer first (every page), then this page's answers
        'page_obj':         page_obj,
        'total_answers':    post.class_comments.count(),
        'answer_sort':      sort,
        'answer_sort_options': QA_ANSWER_SORT_CHOICES,
        'like_total':       post.like.count(),
        'user_liked':       post.like.filter(pk=user.pk).exists(),
        'is_saved':         post.bookmarked_by.filter(pk=user.pk).exists(),
        'user_reported':    ClassPostReport.objects.filter(reporter=user, post=post).exists(),
        'is_asker':         is_asker,
        'is_teacher':       teacher,
        'can_edit_post':    is_asker,
        'can_delete_post':  is_asker or teacher,
        'can_resolve':      is_asker or teacher,
        'can_report_post':  not is_asker,
        'post_reports':     post_reports,
        'report_reasons':   ClassPostReport.REASON_CHOICES,
        'max_answer_chars': QA_ANSWER_MAX,
        'form_subjects':    _qa_subject_names() + ([post.subject] if post.subject and post.subject not in _qa_subject_names() else []),
        'level_options':    CLASS_LEVEL_CHOICES,
        'limits':           QA_FORM_LIMITS,
    }
    return render(request, 'chat_post_detail.html', context)


# ── Answers ─────────────────────────────────────────────────────────

@login_required
@require_POST
def chat_post_comment(request, post_id):
    """Post an answer (text, image and/or audio). Everything is validated before anything is saved."""
    post = get_object_or_404(ClassPost, post_id=post_id)
    detail_url = reverse('chat_post_detail', args=[post.post_id])

    text  = (request.POST.get('comment') or '').strip()
    image = request.FILES.get('image')
    audio = request.FILES.get('audio')

    if not (text or image or audio):
        return _qa_fail(request, 'Write an answer, or attach an image or audio.', detail_url)
    if len(text) > QA_ANSWER_MAX:
        return _qa_fail(request, f'Your answer is too long (maximum {QA_ANSWER_MAX} characters).', detail_url)
    if image:
        err = _check_image_upload(image)
        if err:
            return _qa_fail(request, err, detail_url)
    if audio:
        err = _check_audio_upload(audio)
        if err:
            return _qa_fail(request, err, detail_url)

    comment = ClassPostComment.objects.create(
        post=post, commentator=request.user, comment=text,
        image=image or None, audio=audio or None,
    )

    # Notify the asker and anyone who saved the question (never the person answering).
    me = request.user
    recipients = set(post.bookmarked_by.exclude(pk=me.pk).values_list('pk', flat=True))
    asker_gets = post.author_id != me.pk
    if asker_gets:
        recipients.add(post.author_id)
    title = post.title[:50]
    notes = []
    for uid in recipients:
        if uid == post.author_id:
            msg = f'{_display_name(me)} answered your question: "{title}"'
        else:
            msg = f'New answer on a question you saved: "{title}"'
        notes.append(Notification(user_id=uid, class_post=post, message=msg))
    if notes:
        Notification.objects.bulk_create(notes)

    messages.success(request, 'Your answer has been posted.')
    # Newest first, so the answer the user just wrote is on the first page.
    redirect_url = f'{detail_url}?sort=newest#comment-{comment.comment_id}'
    if _wants_json(request):
        return JsonResponse({'success': True, 'comment_id': str(comment.comment_id), 'redirect': redirect_url})
    return redirect(redirect_url)


@login_required
@require_POST
def edit_class_comment(request, comment_id):
    """Edit the text of your own answer."""
    comment = get_object_or_404(ClassPostComment.objects.select_related('post'), comment_id=comment_id)
    if comment.commentator_id != request.user.pk:
        raise PermissionDenied
    anchor = f"{reverse('chat_post_detail', args=[comment.post_id])}#comment-{comment.comment_id}"

    text = (request.POST.get('comment') or '').strip()
    if not text and not comment.has_media:
        return _qa_fail(request, 'An answer cannot be empty.', anchor)
    if len(text) > QA_ANSWER_MAX:
        return _qa_fail(request, f'Your answer is too long (maximum {QA_ANSWER_MAX} characters).', anchor)

    comment.comment = text
    comment.save(update_fields=['comment'])
    messages.success(request, 'Your answer has been updated.')
    return redirect(anchor)


@login_required
@require_POST
def delete_class_comment(request, comment_id):
    """Delete an answer: its author, or a teacher/admin moderating it."""
    comment = get_object_or_404(ClassPostComment.objects.select_related('post'), comment_id=comment_id)
    if not (comment.commentator_id == request.user.pk or _is_teacher(request.user)):
        raise PermissionDenied
    post = comment.post
    if post.accepted_answer_id == comment.pk:
        ClassPost.objects.filter(pk=post.pk).update(accepted_answer=None, is_resolved=False)
    comment.delete()
    messages.success(request, 'The answer has been deleted.')
    return redirect('chat_post_detail', post_id=post.post_id)


@login_required
@require_POST
def accept_answer(request, comment_id):
    """
    Mark one answer as the accepted answer (or un-accept it).
    Allowed for the student who asked the question and for teachers/admins.
    """
    comment = get_object_or_404(ClassPostComment.objects.select_related('post'), comment_id=comment_id)
    post = comment.post
    detail_url = reverse('chat_post_detail', args=[post.post_id])

    if not (post.author_id == request.user.pk or _is_teacher(request.user)):
        return _qa_fail(request, 'Only the student who asked, or a teacher, can accept an answer.',
                        detail_url, status=403)
    if comment.commentator_id == post.author_id:
        return _qa_fail(request, "You can't accept an answer written by the person who asked the question.",
                        detail_url)

    if post.accepted_answer_id == comment.pk:
        ClassPost.objects.filter(pk=post.pk).update(accepted_answer=None, is_resolved=False)
        messages.success(request, 'Accepted answer removed.')
        accepted = False
    else:
        ClassPost.objects.filter(pk=post.pk).update(accepted_answer=comment, is_resolved=True)
        messages.success(request, 'Answer accepted. The question is now resolved.')
        accepted = True
        if comment.commentator_id != request.user.pk:
            _notify(comment.commentator_id, post,
                    f'Your answer was accepted on "{post.title[:50]}"')

    if _wants_json(request):
        return JsonResponse({'success': True, 'accepted': accepted})
    return redirect(f'{detail_url}#comment-{comment.comment_id}')


@login_required
@require_POST
def helpful_vote(request, comment_id):
    """Toggle a "helpful" vote on an answer (not on your own answer)."""
    comment = get_object_or_404(ClassPostComment.objects.select_related('post'), comment_id=comment_id)
    detail_url = reverse('chat_post_detail', args=[comment.post_id])
    if comment.commentator_id == request.user.pk:
        return _qa_fail(request, "You can't vote on your own answer.", detail_url)

    if comment.helpful.filter(pk=request.user.pk).exists():
        comment.helpful.remove(request.user)
        voted = False
    else:
        comment.helpful.add(request.user)
        voted = True

    if _wants_json(request):
        return JsonResponse({'success': True, 'voted': voted, 'total_helpful': comment.helpful.count()})
    return _back_or(request, 'chat_post_detail', comment.post_id)


# ── Likes, bookmarks, resolve ───────────────────────────────────────

@login_required
@require_POST
def chat_post_like(request, post_id):
    """Like/unlike a question."""
    post = get_object_or_404(ClassPost, post_id=post_id)

    if post.like.filter(pk=request.user.pk).exists():
        post.like.remove(request.user)
        liked = False
    else:
        post.like.add(request.user)
        liked = True
        if post.author_id != request.user.pk:
            msg = f'{_display_name(request.user)} liked your question "{post.title[:50]}"'
            if not Notification.objects.filter(user_id=post.author_id, class_post=post,
                                               message=msg, is_read=False).exists():
                _notify(post.author_id, post, msg)

    if _wants_json(request):
        return JsonResponse({'success': True, 'liked': liked, 'total_likes': post.like.count()})
    return _back_or(request, 'chat_room')


@login_required
@require_POST
def chat_comment_like(request, comment_id):
    """Like/unlike an answer."""
    comment = get_object_or_404(ClassPostComment, comment_id=comment_id)

    if comment.like.filter(pk=request.user.pk).exists():
        comment.like.remove(request.user)
        liked = False
    else:
        comment.like.add(request.user)
        liked = True

    if _wants_json(request):
        return JsonResponse({'success': True, 'liked': liked, 'total_likes': comment.like.count()})
    return _back_or(request, 'chat_room')


@login_required
@require_POST
def bookmark_post(request, post_id):
    """Save/unsave a question so it can be found under "Saved"."""
    post = get_object_or_404(ClassPost, post_id=post_id)
    if post.bookmarked_by.filter(pk=request.user.pk).exists():
        post.bookmarked_by.remove(request.user)
        saved = False
    else:
        post.bookmarked_by.add(request.user)
        saved = True
    if _wants_json(request):
        return JsonResponse({'success': True, 'saved': saved})
    return _back_or(request, 'chat_room')


@login_required
@require_POST
def resolve_post(request, post_id):
    """Mark a question resolved/unresolved: the asker or a teacher/admin."""
    post = get_object_or_404(ClassPost, post_id=post_id)
    if not (post.author_id == request.user.pk or _is_teacher(request.user)):
        raise PermissionDenied

    post.is_resolved = not post.is_resolved
    if not post.is_resolved:
        post.accepted_answer = None     # re-opening a question clears its accepted answer
    post.save()

    messages.success(request, 'Question marked as resolved.' if post.is_resolved
                     else 'Question re-opened.')
    if _wants_json(request):
        return JsonResponse({'success': True, 'is_resolved': post.is_resolved})
    return redirect('chat_post_detail', post_id=post_id)


# ── Reporting and moderation ────────────────────────────────────────

def _report_target(kind, obj_id):
    """(post, target, filter-kwargs, owner_id) for a reportable question or answer."""
    if kind == 'post':
        target = get_object_or_404(ClassPost, post_id=obj_id)
        return target, target, {'post': target}, target.author_id
    if kind == 'comment':
        target = get_object_or_404(ClassPostComment.objects.select_related('post'), comment_id=obj_id)
        return target.post, target, {'comment': target}, target.commentator_id
    raise Http404


@login_required
@require_POST
def report_content(request, kind, obj_id):
    """Report a question or an answer to the teachers."""
    post, target, flt, owner_id = _report_target(kind, obj_id)
    detail_url = reverse('chat_post_detail', args=[post.post_id])

    reason = (request.POST.get('reason') or '').strip()
    details = ' '.join((request.POST.get('details') or '').split())[:300]
    if reason not in dict(ClassPostReport.REASON_CHOICES):
        return _qa_fail(request, 'Choose a reason for your report.', detail_url)
    if owner_id == request.user.pk:
        return _qa_fail(request, "You can't report your own content.", detail_url)
    if ClassPostReport.objects.filter(reporter=request.user, **flt).exists():
        messages.info(request, 'You have already reported this. A teacher will review it.')
        return redirect(detail_url)

    already_flagged = ClassPostReport.objects.filter(is_handled=False, **flt).exists()
    try:
        with transaction.atomic():
            ClassPostReport.objects.create(reporter=request.user, reason=reason, details=details, **flt)
    except IntegrityError:
        messages.info(request, 'You have already reported this. A teacher will review it.')
        return redirect(detail_url)

    # Tell the teachers once per flagged item; the report itself is shown on the question page.
    if not already_flagged:
        what = 'question' if kind == 'post' else 'answer'
        teachers = (User.objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))
                    .exclude(pk=request.user.pk).values_list('pk', flat=True))
        Notification.objects.bulk_create([
            Notification(user_id=uid, class_post=post,
                         message=f'A {what} was reported on "{post.title[:50]}"')
            for uid in teachers
        ])

    messages.success(request, 'Thank you. Your report has been sent to the teachers.')
    return redirect(detail_url)


@login_required
@require_POST
def clear_reports(request, kind, obj_id):
    """Teachers/admins: dismiss the open reports on a question or answer."""
    if not _is_teacher(request.user):
        raise PermissionDenied
    post, target, flt, owner_id = _report_target(kind, obj_id)
    ClassPostReport.objects.filter(is_handled=False, **flt).update(is_handled=True)
    messages.success(request, 'Reports dismissed.')
    return redirect('chat_post_detail', post_id=post.post_id)


# ════════════════════════════════════════════════════════════════════
#  ADMIN DASHBOARD (staff only)
# ════════════════════════════════════════════════════════════════════

PAGE_SIZE = 15
MAX_IMPORT_ROWS = 500
_PW_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


# ── helpers ─────────────────────────────────────────────────────────────

def staff_required(view):
    """Signed-in staff / superusers only. JSON 403 for API paths, 403 page otherwise."""
    @wraps(view)
    @login_required
    @never_cache
    def wrapper(request, *args, **kwargs):
        u = request.user
        if not (u.is_staff or u.is_superuser):
            if request.path.startswith('/dashboard/api/'):
                return JsonResponse({'ok': False, 'error': 'Permission denied.'}, status=403)
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _temp_password(n=8):
    return ''.join(secrets.choice(_PW_ALPHABET) for _ in range(n))


def _guard(request, target, destructive=False):
    """Return an error string if request.user may not act on target, else None."""
    me = request.user
    if (target.is_staff or target.is_superuser) and not me.is_superuser:
        return 'Only a superuser can manage staff accounts.'
    if destructive and target.pk == me.pk:
        return 'You cannot do that to your own account.'
    if destructive and target.is_superuser:
        return 'Superuser accounts must be managed from the Django admin.'
    return None


def _err(msg, status=400):
    return JsonResponse({'ok': False, 'error': msg}, status=status)


def _serialize(u):
    profile = getattr(u, 'profile', None)
    score = getattr(u, 'cbt_score', None)
    subjects = [r.subject_name for r in u.registered_subjects.all()]
    return {
        'id': u.pk,
        'username': u.username,
        'email': u.email,
        'first_name': u.first_name,
        'last_name': u.last_name,
        'full_name': u.get_full_name() or u.username,
        'is_active': u.is_active,
        'joined': localtime(u.date_joined).strftime('%d %b %Y'),
        'last_login': localtime(u.last_login).strftime('%d %b %Y') if u.last_login else 'Never',
        'picture': profile.get_picture_url if profile else '',
        'admission_number': profile.admission_number if profile else '',
        'class_level': profile.class_level if profile else '',
        'school': profile.school if profile else '',
        'gender': profile.gender if profile else '',
        'phone': profile.phone if profile else '',
        'specialization': profile.specialization if profile else '',
        'career_goal': profile.career_goal if profile else '',
        'location': profile.location if profile else '',
        'address': profile.address if profile else '',
        'bio': profile.bio if profile else '',
        'date_of_birth': profile.date_of_birth.isoformat() if profile and profile.date_of_birth else '',
        'subjects': subjects,
        'points': score.points if score else 0,
        'exams': getattr(u, 'exam_count', None) if hasattr(u, 'exam_count') else CBTExam.objects.filter(student=u).count(),
    }


def _clean_subjects(names):
    seen = []
    for n in names:
        n = (n or '').strip()
        if n in SENIOR_SECONDARY_SUBJECTS and n not in seen:
            seen.append(n)
    return seen


def _validate_identity(username, email, exclude_pk=None, username_optional=False):
    users = User.objects.all()
    if exclude_pk:
        users = users.exclude(pk=exclude_pk)
    # A blank username is allowed when creating a student: it is auto-set to the Admission No.
    if username or not username_optional:
        if len(username) < 5:
            return 'Username must be at least 5 characters.'
        if ' ' in username:
            return 'Username cannot contain spaces.'
        if users.filter(username__iexact=username).exists():
            return f'Username "{username}" is already taken.'
    if not email:
        return 'Email address is required.'
    try:
        validate_email(email)
    except ValidationError:
        return 'Please enter a valid email address.'
    if users.filter(email__iexact=email).exists():
        return f'Email "{email}" is already registered.'
    return None


def _create_student(data, files, subjects):
    """Create user + profile + score row + subjects atomically. Returns (user, password)."""
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    fname = (data.get('fname') or '').strip()
    lname = (data.get('lname') or '').strip()
    password = data.get('password') or ''

    err = _validate_identity(username, email, username_optional=True)
    if err:
        raise ValueError(err)
    if not fname or not lname:
        raise ValueError('First name and last name are required.')
    if not password:
        password = _temp_password()
    if len(password) < 6:
        raise ValueError('Password must be at least 6 characters.')

    active = str(data.get('is_active', '1')) != '0'
    username_generated = not username
    with transaction.atomic():
        user = User.objects.create_user(
            # placeholder only; replaced by the Admission No below once the user id exists
            username=username or f'tmp-{uuid.uuid4().hex}', email=email, first_name=fname,
            last_name=lname, password=password, is_active=active,
        )
        profile = Profile.objects.create(user=user, student_category='senior')
        if username_generated:
            # Admission No is TMTC/<year>/<id>; "/" can't be used in a username because
            # the profile URL (<str:username>) wouldn't match, so we store TMTC-<year>-<id>.
            auto = profile.admission_number.replace('/', '-')
            if User.objects.filter(username__iexact=auto).exclude(pk=user.pk).exists():
                auto = f'{auto}-{uuid.uuid4().hex[:4]}'
            user.username = auto
            user.save(update_fields=['username'])
        CBTScore.objects.get_or_create(user=user)
        err = _apply_profile_updates(user, profile, data, files)
        if err:
            raise ValueError(err)           # rolls the whole creation back
        RegisteredSubject.objects.bulk_create([
            RegisteredSubject(student=user, category='senior', subject_name=n)
            for n in _clean_subjects(subjects)
        ], ignore_conflicts=True)
    user.username_generated = username_generated
    return user, password


def _csv_safe(v):
    v = '' if v is None else str(v)
    return "'" + v if v[:1] in ('=', '+', '-', '@') else v


# ── page ────────────────────────────────────────────────────────────────

@staff_required
def dashboard_home(request):
    students = User.objects.filter(is_staff=False, is_superuser=False)
    now = timezone.now()

    stats = {
        'total': students.count(),
        'active': students.filter(is_active=True).count(),
        'inactive': students.filter(is_active=False).count(),
        'week': students.filter(date_joined__gte=now - timedelta(days=7)).count(),
        'exams': CBTExam.objects.count(),
        'avg': round(CBTExam.objects.aggregate(a=Avg('percentage'))['a'] or 0),
        'questions': ClassPost.objects.count(),
        'live': LiveSession.objects.filter(is_active=True).count(),
    }

    counter = Counter()
    for lvl in Profile.objects.filter(user__in=students).values_list('class_level', flat=True):
        norm = _normalize_class_level(lvl)
        counter[norm[:3] if norm else 'Not set'] += 1
    max_n = max(counter.values()) if counter else 1
    class_dist = [
        {'label': k, 'n': counter[k], 'pct': round(counter[k] / max_n * 100)}
        for k in sorted(counter)
    ]

    top = (CBTScore.objects.filter(user__in=students, points__gt=0)
           .select_related('user', 'user__profile').order_by('-points')[:5])
    top_students = [{
        'username': s.user.username,
        'name': s.user.get_full_name() or s.user.username,
        'points': s.points,
        'picture': s.user.profile.get_picture_url,
    } for s in top]

    recent = (students.select_related('profile').order_by('-date_joined')[:5])
    recent_students = [{
        'username': u.username,
        'name': u.get_full_name() or u.username,
        'joined': localtime(u.date_joined).strftime('%d %b %Y'),
        'picture': u.profile.get_picture_url if hasattr(u, 'profile') else '',
    } for u in recent]

    return render(request, 'admin_dashboard.html', {
        'stats': stats,
        'class_dist': class_dist,
        'top_students': top_students,
        'recent_students': recent_students,
        'subjects': SENIOR_SECONDARY_SUBJECTS,
        'class_levels': CLASS_LEVEL_CHOICES,
        'gender_choices': GENDER_CHOICES,
        'specializations': [c[0] for c in Profile.SPECIALIZATION_CHOICES],
    })


# ── students API ────────────────────────────────────────────────────────

@staff_required
@require_GET
def api_students(request):
    qs = (User.objects.filter(is_staff=False, is_superuser=False)
          .select_related('profile', 'cbt_score')
          .prefetch_related('registered_subjects')
          .annotate(exam_count=Count('cbt_exams', distinct=True)))

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(email__icontains=q) |
            Q(first_name__icontains=q) | Q(last_name__icontains=q) |
            Q(profile__school__icontains=q) | Q(profile__phone__icontains=q)
        )
    cls = request.GET.get('class', '').strip()
    if cls:
        qs = qs.filter(profile__class_level__istartswith=cls)
    status = request.GET.get('status', '')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)

    qs = qs.order_by({
        'oldest': 'date_joined', 'name': 'first_name', 'points': '-cbt_score__points',
    }.get(request.GET.get('sort', 'newest'), '-date_joined'), 'pk')

    try:
        page_no = int(request.GET.get('page', 1))
    except ValueError:
        page_no = 1
    page = Paginator(qs, PAGE_SIZE).get_page(page_no)
    return JsonResponse({
        'ok': True,
        'students': [_serialize(u) for u in page.object_list],
        'page': page.number,
        'pages': page.paginator.num_pages,
        'count': page.paginator.count,
    })


@staff_required
@require_POST
def api_student_create(request):
    try:
        user, password = _create_student(request.POST, request.FILES, request.POST.getlist('subjects'))
    except ValueError as exc:
        return _err(str(exc))
    return JsonResponse({
        'ok': True, 'username': user.username, 'password': password,
        'admission_number': user.profile.admission_number,
        'generated': not request.POST.get('password'),
        'username_generated': getattr(user, 'username_generated', False),
    })


@staff_required
@require_POST
def api_student_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    guard = _guard(request, user)
    if guard:
        return _err(guard, 403)

    username = (request.POST.get('username') or user.username).strip()
    email = (request.POST.get('email') or user.email).strip().lower()
    err = _validate_identity(username, email, exclude_pk=user.pk)
    if err:
        return _err(err)

    new_password = request.POST.get('password') or ''
    if new_password and len(new_password) < 6:
        return _err('Password must be at least 6 characters.')

    wants_active = request.POST.get('is_active', '1') != '0'
    if not wants_active and user.pk == request.user.pk:
        return _err('You cannot deactivate your own account.')

    profile, _ = Profile.objects.get_or_create(user=user)
    profile.user = user          # same instance, so Profile.save() persists our user edits too
    try:
        with transaction.atomic():
            user.username, user.email, user.is_active = username, email, wants_active
            if new_password:
                user.set_password(new_password)
            err = _apply_profile_updates(user, profile, request.POST, request.FILES)
            if err:
                raise ValueError(err)
            if profile.student_category != 'senior':
                profile.student_category = 'senior'
                profile.save()
            if request.POST.get('subjects_sent'):
                RegisteredSubject.objects.filter(student=user).delete()
                RegisteredSubject.objects.bulk_create([
                    RegisteredSubject(student=user, category='senior', subject_name=n)
                    for n in _clean_subjects(request.POST.getlist('subjects'))
                ])
    except ValueError as exc:
        return _err(str(exc))
    return JsonResponse({'ok': True})


@staff_required
@require_POST
def api_student_delete(request, pk):
    user = get_object_or_404(User, pk=pk)
    guard = _guard(request, user, destructive=True)
    if guard:
        return _err(guard, 403)
    name = user.username
    user.delete()
    return JsonResponse({'ok': True, 'deleted': name})


@staff_required
@require_POST
def api_student_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    guard = _guard(request, user, destructive=True)
    if guard:
        return _err(guard, 403)
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    return JsonResponse({'ok': True, 'is_active': user.is_active})


@staff_required
@require_POST
def api_student_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    guard = _guard(request, user, destructive=True)
    if guard:
        return _err(guard, 403)
    password = request.POST.get('password') or ''
    generated = not password
    if generated:
        password = _temp_password()
    elif len(password) < 6:
        return _err('Password must be at least 6 characters.')
    elif len(password) > 128:
        return _err('Password is too long (max 128 characters).')
    user.set_password(password)
    user.save(update_fields=['password'])
    return JsonResponse({'ok': True, 'username': user.username, 'password': password, 'generated': generated})


@staff_required
@require_POST
def api_students_bulk(request):
    action = request.POST.get('action')
    ids = [i for i in request.POST.getlist('ids') if i.isdigit()]
    if action not in ('delete', 'activate', 'deactivate') or not ids:
        return _err('Choose an action and at least one student.')
    done, skipped = 0, 0
    for user in User.objects.filter(pk__in=ids):
        if _guard(request, user, destructive=True):
            skipped += 1
            continue
        if action == 'delete':
            user.delete()
        else:
            user.is_active = (action == 'activate')
            user.save(update_fields=['is_active'])
        done += 1
    return JsonResponse({'ok': True, 'done': done, 'skipped': skipped})


# ── exam results ────────────────────────────────────────────────────────

@staff_required
@require_GET
def api_student_results(request, pk):
    user = get_object_or_404(User, pk=pk)
    labels = dict(CBTExam.SUBJECT_CHOICES)
    exams = CBTExam.objects.filter(student=user).order_by('-taken_at')[:100]
    return JsonResponse({
        'ok': True,
        'student': user.get_full_name() or user.username,
        'exams': [{
            'id': str(e.exam_id),
            'subject': labels.get(e.subject, e.subject.title()),
            'score': e.score, 'total': e.total, 'percentage': e.percentage,
            'grade': e.get_grade_display(), 'time': e.time_display, 'type': e.get_exam_type_display(),
            'date': localtime(e.taken_at).strftime('%d %b %Y %H:%M'),
        } for e in exams],
    })


@staff_required
@require_POST
def api_exam_delete(request, exam_id):
    exam = get_object_or_404(CBTExam, exam_id=exam_id)
    user = exam.student
    exam.delete()
    score, _ = CBTScore.objects.get_or_create(user=user)
    score.recalculate()
    return JsonResponse({'ok': True, 'points': score.points})


@staff_required
@require_POST
def api_student_clear_results(request, pk):
    user = get_object_or_404(User, pk=pk)
    guard = _guard(request, user)
    if guard:
        return _err(guard, 403)
    n, _ = CBTExam.objects.filter(student=user).delete()
    score, _ = CBTScore.objects.get_or_create(user=user)
    score.recalculate()
    return JsonResponse({'ok': True, 'deleted': n})


# ── CSV export / import ─────────────────────────────────────────────────

@staff_required
@require_GET
def api_students_export(request):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="students-{timezone.now():%Y%m%d}.csv"'
    w = csv.writer(resp)
    w.writerow(['admission_no', 'username', 'first_name', 'last_name', 'email', 'class_level',
                'school', 'gender', 'phone', 'specialization', 'career_goal', 'subjects',
                'points', 'active', 'joined'])
    qs = (User.objects.filter(is_staff=False, is_superuser=False)
          .select_related('profile', 'cbt_score').prefetch_related('registered_subjects')
          .order_by('date_joined'))
    for u in qs:
        p = getattr(u, 'profile', None)
        s = getattr(u, 'cbt_score', None)
        w.writerow([_csv_safe(x) for x in [
            p.admission_number if p else '', u.username, u.first_name, u.last_name, u.email,
            p.class_level if p else '', p.school if p else '', p.gender if p else '',
            p.phone if p else '', p.specialization if p else '', p.career_goal if p else '',
            '; '.join(r.subject_name for r in u.registered_subjects.all()),
            s.points if s else 0, 'yes' if u.is_active else 'no',
            localtime(u.date_joined).strftime('%Y-%m-%d'),
        ]])
    return resp


@staff_required
@require_POST
def api_students_import(request):
    """
    CSV columns (header row required): email, first_name, last_name
    optional: username (blank = Admission No), class_level, school, gender, phone, specialization, career_goal,
              subjects (separated by ;), password (blank = auto-generated)
    """
    upload = request.FILES.get('file')
    if not upload:
        return _err('Please choose a CSV file.')
    if upload.size > 2 * 1024 * 1024:
        return _err('CSV file is too large (max 2 MB).')
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return _err('Could not read the file. Save it as UTF-8 CSV.')

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return _err('The CSV file is empty.')
    reader.fieldnames = [(h or '').strip().lower() for h in reader.fieldnames]
    required = {'email', 'first_name', 'last_name'}
    if not required.issubset(reader.fieldnames):
        return _err('Missing columns. Required: ' + ', '.join(sorted(required)))

    created, errors = [], []
    for i, row in enumerate(reader, start=2):      # row 1 is the header
        if i - 1 > MAX_IMPORT_ROWS:
            errors.append(f'Stopped after {MAX_IMPORT_ROWS} rows.')
            break
        row = {k: (v or '').strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue
        data = {
            'username': row.get('username', ''), 'email': row.get('email', ''),
            'fname': row.get('first_name', ''), 'lname': row.get('last_name', ''),
            'password': row.get('password', ''),
        }
        for key in ('class_level', 'school', 'gender', 'phone', 'specialization', 'career_goal'):
            if row.get(key):
                data[key] = row[key]
        subjects = [s for s in row.get('subjects', '').split(';') if s.strip()]
        try:
            user, pw = _create_student(data, {}, subjects)
            created.append({'username': user.username, 'password': pw})
        except ValueError as exc:
            errors.append(f'Row {i} ({row.get("username") or row.get("email") or "blank"}): {exc}')
    return JsonResponse({'ok': True, 'created': created, 'errors': errors})


# ── content moderation ──────────────────────────────────────────────────

@staff_required
@require_GET
def api_content(request):
    q = request.GET.get('q', '').strip()
    qs = ClassPost.objects.select_related('author').order_by('-created_at')
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))
    items = [{
        'id': str(p.post_id), 'title': p.title, 'subject': p.subject,
        'author': p.author.username, 'meta': f'{p.total_comments} replies · {p.view} views',
        'date': localtime(p.created_at).strftime('%d %b %Y'),
    } for p in qs[:60]]
    return JsonResponse({'ok': True, 'items': items})


@staff_required
@require_POST
def api_content_delete(request):
    try:
        obj = ClassPost.objects.get(post_id=request.POST.get('id'))
    except (ClassPost.DoesNotExist, ValueError, ValidationError):
        return _err('Item not found.', 404)
    obj.delete()
    return JsonResponse({'ok': True})
