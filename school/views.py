from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User, auth
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from school.models import Profile, Post, PostComment, Notification, LiveSession, LiveParticipant, CBTExam, CBTScore, ClassPost, ClassPostComment, RegisteredSubject
from django.db.models import Q
import uuid, json, re, logging
from datetime import date as date_type
from django.db import transaction
from django.views.decorators.cache import never_cache
import csv, io, secrets
from collections import Counter
from datetime import timedelta
from functools import wraps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db.models import Avg, Count
from django.http import HttpResponse
from django.utils import timezone
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


def _build_top_students_overall():
    top_scores = (
        CBTScore.objects
        .select_related('user', 'user__profile')
        .filter(points__gt=0)
        .order_by('-points')[:5]
    )
    result = []
    for position, score in enumerate(top_scores, start=1):
        level_name, level_num = _get_student_level(score.points)
        result.append({
            'position':   position,
            'user':       score.user,
            'profile':    score.user.profile,
            'points':     score.points,
            'stat_val':   score.points,
            'stat_label': 'pts',
            'level_name': level_name,
            'level_num':  level_num,
        })
    return result


def _build_top_students_subject(subject):
    """Return top-5 students for a specific subject ranked by best percentage."""
    from django.db.models import Max
    top_exams = (
        CBTExam.objects
        .filter(subject=subject)
        .values('student')
        .annotate(best_pct=Max('percentage'))
        .order_by('-best_pct')[:5]
    )
    result = []
    for position, entry in enumerate(top_exams, start=1):
        try:
            user    = User.objects.select_related('profile').get(pk=entry['student'])
            profile = user.profile
        except (User.DoesNotExist, Profile.DoesNotExist):
            continue
        try:
            cbt_score = CBTScore.objects.get(user=user)
            points    = cbt_score.points
        except CBTScore.DoesNotExist:
            points = 0
        level_name, level_num = _get_student_level(points)
        result.append({
            'position':   position,
            'user':       user,
            'profile':    profile,
            'points':     points,
            'stat_val':   entry['best_pct'],
            'stat_label': '%',
            'level_name': level_name,
            'level_num':  level_num,
        })
    return result


def home(request):
    top_students    = _build_top_students_overall()
    top_mathematics = _build_top_students_subject('mathematics')
    top_physics     = _build_top_students_subject('physics')

    return render(request, 'home.html', {
        'top_students':    top_students,
        'top_mathematics': top_mathematics,
        'top_physics':     top_physics,
    })


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
    exam_list = list(all_exams[:50]) if can_view_private else []

    def _band(pct):
        return 'high' if pct >= 70 else 'mid' if pct >= 50 else 'low'

    exam_history = []
    for ex in exam_list:
        subject_label = subject_labels.get(ex.subject, ex.subject.replace('_', ' ').title())
        # "feedback"/"title" are optional extras: shown automatically if you
        # ever add such fields to CBTExam, without needing any change here.
        teacher_feedback = (getattr(ex, 'feedback', '') or getattr(ex, 'teacher_feedback', '') or '').strip()
        test_name = (getattr(ex, 'title', '') or getattr(ex, 'test_name', '') or f'{subject_label} CBT Test')
        exam_history.append({
            'test_name':        test_name,
            'subject':          subject_label,
            'date':             ex.taken_at,
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
def update_profile(request, username):
    """Classic (non-AJAX) profile form. Students may only edit their own profile."""
    if request.user.username != username:
        messages.error(request, 'You can only edit your own profile.')
        return redirect('profile', username=request.user.username)

    user    = request.user
    profile = user.profile
    if request.method == 'POST':
        error = _apply_profile_updates(user, profile, request.POST, request.FILES)
        if error:
            messages.error(request, error)
            return render(request, 'updateprofile.html', {'profile': profile})
        messages.info(request, 'Profile Updated')
        return redirect('profile', username=username)
    return render(request, 'updateprofile.html', {'profile': profile})


@login_required
@require_POST
def edit_profile(request):
    """
    AJAX endpoint for the profile page's Edit Profile modal.
    Accepts multipart/form-data: fname, lname, gender, phone, bio, location, address,
    school, class_level, specialization, career_goal, date_of_birth, image (file).
    Always acts on the signed-in student. Returns JSON { success, error? }.
    """
    user    = request.user
    profile = user.profile
    try:
        error = _apply_profile_updates(user, profile, request.POST, request.FILES)
    except Exception:
        logger.exception('edit_profile failed for user %s', user.pk)
        return JsonResponse({'success': False, 'error': 'Could not save your changes. Please try again.'}, status=500)

    if error:
        return JsonResponse({'success': False, 'error': error}, status=400)
    return JsonResponse({'success': True})


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
            'error': 'Your class level is not a Senior Secondary class. Please set it to SS1, SS2 or SS3 in Edit Profile first.',
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

# RegisteredSubject.subject_name  ->  subject id used by the picker in cbt_exams.html
REGISTERED_TO_CBT_ID = {
    'Mathematics': 'maths', 'English Language': 'english', 'Physics': 'physics',
    'Chemistry': 'chemistry', 'Biology': 'biology', 'Economics': 'economics',
    'Government': 'government', 'Literature in English': 'literature',
    'Geography': 'geography', 'Further Mathematics': 'furthermaths',
    'Agricultural Science': 'agric', 'Accounting': 'accounting', 'Commerce': 'commerce',
    'Christian Religious Studies': 'crk', 'Islamic Religious Studies': 'irk',
    'Civic Education': 'civicedu', 'Computer Studies': 'computer', 'French': 'french',
    'Technical Drawing': 'techdrawing',
}


@login_required
@ensure_csrf_cookie
def cbt_subjects(request):
    """Render the CBT subject picker page, pre-selecting the student's registered subjects."""
    names = RegisteredSubject.objects.filter(student=request.user).values_list('subject_name', flat=True)
    preselected = [REGISTERED_TO_CBT_ID[n] for n in names if n in REGISTERED_TO_CBT_ID]

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
        'preselected_subjects': preselected,
        'student_info': student_info,
    })


@login_required
def cbt_mathematics(request):
    """Render the CBT exam page (Mathematics default)."""
    return render(request, 'cbt_mathematics.html')


# kept for backwards compat — alias used by cbt_exam url
cbt_exam = cbt_mathematics


@login_required
def cbt_physics(request):
    """Render the original Physics CBT exam page (unchanged)."""
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
    aliases = {'maths': 'mathematics'}

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
            subject = aliases.get(subject, subject)
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


# ── Student Scores Modal (AJAX / JSON) ──────────────────────────

@login_required
def student_scores_modal(request, username):
    import math
    from django.db.models import Max, Avg, Sum, Count
    from django.utils.timezone import localtime

    user       = get_object_or_404(User, username=username)
    profile    = get_object_or_404(Profile, user=user)
    cbt_score, _ = CBTScore.objects.get_or_create(user=user)
    level_name, level_num = _get_student_level(cbt_score.points)

    # ── per-subject aggregates ────────────────────────────────────
    all_exams = CBTExam.objects.filter(student=user)

    subjects_data = []
    subject_names = all_exams.values_list('subject', flat=True).distinct()

    GRADE_COLOR = {
        'distinction': '#16a34a',
        'credit':      '#2563eb',
        'pass':        '#d97706',
        'nearly':      '#ea580c',
        'fail':        '#dc2626',
    }

    for subj in subject_names:
        subj_qs = all_exams.filter(subject=subj)
        agg     = subj_qs.aggregate(
            best_pct=Max('percentage'),
            avg_pct=Avg('percentage'),
            attempts=Count('exam_id'),
            total_correct=Sum('score'),
            total_questions=Sum('total'),
        )

        best_exam = subj_qs.order_by('-percentage').first()
        last_exam = subj_qs.order_by('-taken_at').first()

        best_grade = best_exam.grade if best_exam else 'fail'
        best_pct   = round(agg['best_pct'] or 0)
        avg_pct    = round(agg['avg_pct'] or 0)

        # subject rating: weighted combo of best + avg
        subject_rating = min(100, round(best_pct * 0.6 + avg_pct * 0.4))

        # time efficiency: full marks in half the allowed time = 100 %
        if best_exam and best_exam.time_used_sec and best_exam.total:
            allowed_sec = best_exam.total * 60
            time_eff    = min(100, round((1 - best_exam.time_used_sec / allowed_sec) * 100 + 50))
        else:
            time_eff = 0

        # format best time
        if best_exam and best_exam.time_used_sec:
            m, s = divmod(best_exam.time_used_sec, 60)
            time_display = f'{m}m {s:02d}s'
        else:
            time_display = '—'

        last_taken = (
            localtime(last_exam.taken_at).strftime('%d %b %Y')
            if last_exam else '—'
        )

        subjects_data.append({
            'subject':        subj.capitalize(),
            'attempts':       agg['attempts'],
            'best_score':     best_exam.score if best_exam else 0,
            'best_total':     best_exam.total if best_exam else 0,
            'best_pct':       best_pct,
            'best_grade':     best_grade.capitalize(),
            'grade_color':    GRADE_COLOR.get(best_grade, '#606060'),
            'avg_pct':        avg_pct,
            'subject_rating': subject_rating,
            'best_time_eff':  time_eff,
            'time_display':   time_display,
            'last_taken':     last_taken,
        })

    # sort subjects by best_pct descending
    subjects_data.sort(key=lambda x: x['best_pct'], reverse=True)

    # ── recent exams (last 10) ────────────────────────────────────
    recent_exams_data = []
    for ex in all_exams.order_by('-taken_at')[:10]:
        if ex.time_used_sec:
            m, s = divmod(ex.time_used_sec, 60)
            t_display = f'{m}m {s:02d}s'
        else:
            t_display = '—'

        # per-exam rating
        ex_rating = min(100, round(ex.percentage * 0.6 + ex.percentage * 0.4))

        recent_exams_data.append({
            'subject':      ex.subject.capitalize(),
            'score':        ex.score,
            'total':        ex.total,
            'percentage':   ex.percentage,
            'grade':        ex.grade.capitalize(),
            'rating':       ex_rating,
            'time_display': t_display,
            'taken_at':     localtime(ex.taken_at).strftime('%d %b %Y'),
        })

    # ── overall accuracy ─────────────────────────────────────────
    totals = all_exams.aggregate(
        total_correct=Sum('score'),
        total_questions=Sum('total'),
    )
    total_correct   = totals['total_correct']   or 0
    total_questions = totals['total_questions'] or 0

    overall_rating = min(100, round(
        (cbt_score.best_score * 0.5 + (total_correct / total_questions * 100 if total_questions else 0) * 0.5)
    ))

    # ── profile fields (optional — Profile may not have these) ───
    def safe_get(obj, attr):
        return getattr(obj, attr, None) or ''

    return JsonResponse({
        'ok': True,
        'student': {
            'username':        user.username,
            'full_name':       user.get_full_name() or user.username,
            'picture_url':     profile.get_picture_url,
            'points':          cbt_score.points,
            'best_score':      cbt_score.best_score,
            'total_attempts':  cbt_score.total_attempts,
            'total_correct':   total_correct,
            'total_questions': total_questions,
            'overall_rating':  overall_rating,
            'level_name':      level_name,
            'level_num':       level_num,
            'grade':           safe_get(profile, 'grade'),
            'specialization':  safe_get(profile, 'specialization'),
            'school_type':     safe_get(profile, 'school_type'),
        },
        'subjects':     subjects_data,
        'recent_exams': recent_exams_data,
    })


# ── Chat Room / Class Discussion Views ──────────────────────────────

@login_required
def chat_room(request):
    """
    Main chat room page — supports sort, subject/status filters,
    trending posts, live stats, and a lightweight count-polling endpoint
    for the new-posts banner.
    """
    from django.db.models import Count
    from django.utils import timezone
    import datetime

    # ── Count-only polling endpoint (for the new-posts banner) ────
    # Called by JS every 60s with ?_count=1 to check if new posts exist.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('_count'):
        filter_subject = request.GET.get('subject', '')
        filter_status  = request.GET.get('status', '')
        qs = ClassPost.objects.all()
        if filter_subject:
            qs = qs.filter(subject__iexact=filter_subject)
        if filter_status == 'resolved':
            qs = qs.filter(is_resolved=True)
        elif filter_status == 'unresolved':
            qs = qs.filter(is_resolved=False)
        return JsonResponse({'count': qs.count()})

    # ── Query params ───────────────────────────────────────────────
    filter_subject = request.GET.get('subject', '')
    filter_status  = request.GET.get('status', '')
    sort_by        = request.GET.get('sort', 'latest')   # latest | popular | unanswered

    # ── Subject list for filter chips ─────────────────────────────
    subjects = ClassPost.objects.values_list('subject', flat=True).distinct()
    subject_list = sorted([s for s in subjects if s])

    # ── Base queryset ──────────────────────────────────────────────
    posts = ClassPost.objects.select_related(
        'author', 'author__profile'
    ).prefetch_related('like', 'class_comments')

    if filter_subject:
        posts = posts.filter(subject__iexact=filter_subject)
    if filter_status == 'resolved':
        posts = posts.filter(is_resolved=True)
    elif filter_status == 'unresolved':
        posts = posts.filter(is_resolved=False)

    # ── Sort ───────────────────────────────────────────────────────
    if sort_by == 'popular':
        # Rank by likes first, then views
        posts = posts.annotate(like_count=Count('like')).order_by('-like_count', '-view')
    elif sort_by == 'unanswered':
        # Only posts with zero replies, newest first
        posts = posts.annotate(reply_count=Count('class_comments')).filter(reply_count=0).order_by('-created_at')
    else:
        # Default: latest
        posts = posts.order_by('-created_at')

    # ── Stats for the stats bar ────────────────────────────────────
    all_posts      = ClassPost.objects.all()
    resolved_count = all_posts.filter(is_resolved=True).count()

    today_start  = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    active_today = all_posts.filter(created_at__gte=today_start).count()

    # ── Trending strip (top 6 by engagement in last 7 days) ───────
    seven_days_ago = timezone.now() - datetime.timedelta(days=7)
    trending_posts = (
        ClassPost.objects
        .filter(created_at__gte=seven_days_ago)
        .annotate(
            like_count=Count('like'),
            reply_count=Count('class_comments'),
        )
        .order_by('-like_count', '-reply_count', '-view')
        [:6]
    )

    context = {
        'posts':           posts,
        'subjects':        subject_list,
        'current_subject': filter_subject,
        'current_status':  filter_status,
        'current_sort':    sort_by,
        'resolved_count':  resolved_count,
        'active_today':    active_today,
        'trending_posts':  trending_posts,
    }
    return render(request, 'chat_room.html', context)


@login_required
def create_class_post(request):
    """Create a new question/post in the chat room"""
    if request.method == 'POST':
        title   = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        subject = request.POST.get('subject', '').strip()
        image   = request.FILES.get('image')

        if not title or not content:
            messages.error(request, 'Please provide both title and content for your question.')
            return redirect('chat_room')

        post = ClassPost.objects.create(
            author=request.user,
            title=title,
            content=content,
            subject=subject if subject else 'General',
        )

        if image:
            post.image = image
            post.save()

        messages.success(request, 'Your question has been posted!')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'post_id': str(post.post_id),
                'redirect': f'/chat/post/{post.post_id}/'
            })

        return redirect('chat_post_detail', post_id=post.post_id)

    return redirect('chat_room')


@login_required
def chat_post_detail(request, post_id):
    """View a single post with its comments/replies"""
    post           = get_object_or_404(ClassPost, post_id=post_id)
    comments       = ClassPostComment.objects.filter(post=post)
    total_comments = comments.count()

    post.view += 1
    post.save()

    context = {
        'post':           post,
        'comments':       comments,
        'total_comments': total_comments,
    }
    return render(request, 'chat_post_detail.html', context)


@login_required
def chat_post_comment(request, post_id):
    """Add a comment/reply to a chat post with optional image/audio attachments"""
    if request.method == 'POST':
        post = get_object_or_404(ClassPost, post_id=post_id)
        comment_text = request.POST.get('comment', '').strip()
        image_file = request.FILES.get('image')
        audio_file = request.FILES.get('audio')

        # Validate: at least one of text, image, or audio is provided
        if not comment_text and not image_file and not audio_file:
            messages.error(request, 'Please provide a comment, image, or audio.')
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'No content provided'}, status=400)
            return redirect('chat_post_detail', post_id=post_id)

        # Create the comment
        comment = ClassPostComment.objects.create(
            post=post,
            commentator=request.user,
            comment=comment_text if comment_text else ''
        )

        # Handle image upload
        if image_file:
            allowed_image_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
            if image_file.content_type in allowed_image_types:
                comment.image = image_file
            else:
                messages.warning(request, 'Unsupported image format. Please use JPEG, PNG, GIF, or WEBP.')

        # Handle audio upload
        if audio_file:
            allowed_audio_types = ['audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/ogg', 'audio/mp4', 'audio/webm', 'audio/webm;codecs=opus', 'audio/ogg;codecs=opus']
            if audio_file.content_type in allowed_audio_types:
                if audio_file.size > 10 * 1024 * 1024:
                    messages.warning(request, 'Audio file too large. Maximum size is 10MB.')
                else:
                    comment.audio = audio_file
            else:
                messages.warning(request, 'Unsupported audio format. Please use MP3, WAV, or OGG.')

        comment.save()

        # Create notification for post author
        if post.author != request.user:
            notification_message = f'{request.user.username} replied to your question: "{post.title[:50]}"'
            if image_file and not comment_text:
                notification_message = f'{request.user.username} added an image to your question: "{post.title[:50]}"'
            elif audio_file and not comment_text:
                notification_message = f'{request.user.username} added audio to your question: "{post.title[:50]}"'

            Notification.objects.create(
                user=post.author,
                class_post=post,
                message=notification_message
            )

        messages.success(request, 'Reply added!')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            response_data = {
                'success': True,
                'comment_id': str(comment.comment_id),
                'username': request.user.get_full_name() or request.user.username,
                'comment': comment.comment,
                'created_at': comment.created_at.strftime('%d %b %Y, %I:%M %p'),
                'avatar_url': request.user.profile.get_picture_url,
                'has_media': comment.has_media,
            }

            if comment.get_image_url:
                response_data['image_url'] = comment.get_image_url

            if comment.get_audio_url:
                response_data['audio_url'] = comment.get_audio_url

            return JsonResponse(response_data)

    return redirect('chat_post_detail', post_id=post_id)


@login_required
def chat_post_like(request, post_id):
    """Like/unlike a chat post"""
    post = get_object_or_404(ClassPost, post_id=post_id)

    if request.user in post.like.all():
        post.like.remove(request.user)
        liked = False
    else:
        post.like.add(request.user)
        liked = True
        if post.author != request.user:
            Notification.objects.create(
                user=post.author,
                class_post=post,
                message=f'{request.user.username} liked your question "{post.title[:50]}"'
            )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'liked': liked,
            'total_likes': post.like.count()
        })

    return redirect(request.META.get('HTTP_REFERER', 'chat_room'))


@login_required
def chat_comment_like(request, comment_id):
    """Like/unlike a comment/reply"""
    comment = get_object_or_404(ClassPostComment, comment_id=comment_id)

    if request.user in comment.like.all():
        comment.like.remove(request.user)
        liked = False
    else:
        comment.like.add(request.user)
        liked = True

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'liked': liked,
            'total_likes': comment.like.count()
        })

    return redirect(request.META.get('HTTP_REFERER', 'chat_room'))


@login_required
def resolve_post(request, post_id):
    """Mark a question as resolved or unresolved"""
    post = get_object_or_404(ClassPost, post_id=post_id, author=request.user)

    post.is_resolved = not post.is_resolved
    post.save()

    status = "resolved" if post.is_resolved else "unresolved"
    messages.success(request, f'Question marked as {status}!')

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'is_resolved': post.is_resolved
        })

    return redirect('chat_post_detail', post_id=post_id)
    
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
            'grade': e.get_grade_display(), 'time': e.time_display,
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
