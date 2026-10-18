from django.db import models, transaction, IntegrityError
from django.utils import timezone as dj_timezone
from cloudinary.models import CloudinaryField
from django.contrib.auth.models import User
from django.conf import settings
from django.templatetags.static import static
import uuid
import hashlib


# Create your models here.

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    if settings.USE_CLOUDINARY:
        picture = CloudinaryField('picture', folder='profile_image', default='logo_iowyea')
    else:
        picture = models.ImageField(upload_to='profile_image/', default='male.png')

    follow = models.ManyToManyField(User, related_name='profile_like', blank=True)
    gender = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    bio = models.CharField(max_length=300, blank=True)
    address = models.CharField(max_length=200, blank=True)
    about = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=300, blank=True)
    school = models.CharField(max_length=200, blank=True)
    class_level = models.CharField(max_length=50, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    SPECIALIZATION_CHOICES = [
        ('Science',    'Science'),
        ('Arts',       'Arts'),
        ('Commercial', 'Commercial'),
    ]
    specialization = models.CharField(max_length=20, choices=SPECIALIZATION_CHOICES, blank=True)

    STUDENT_CATEGORY_CHOICES = [
        ('senior', 'Senior Secondary (SSS)'),
    ]
    student_category = models.CharField(max_length=10, choices=STUDENT_CATEGORY_CHOICES, blank=True)
    career_goal = models.CharField(max_length=100, blank=True)  # "What I want to become" e.g. Doctor, Nurse, Engineer

    # Student admission number, e.g. TMTC/2026/S/001. Stored so it never changes once issued.
    # Only students get one (never admins/teachers); it is issued as soon as a department is set.
    admission_no = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.user.first_name:
            self.user.first_name = self.user.first_name.capitalize()
        if self.user.last_name:
            self.user.last_name = self.user.last_name.capitalize()
        if self.bio:
            self.bio = self.bio.capitalize()
        if self.about:
            self.about = self.about.capitalize()
        if self.address:
            self.address = self.address.title()
        if self.location:
            self.location = self.location.title()
        if self.school:
            self.school = self.school.title()
        if self.class_level:
            self.class_level = self.class_level.upper()
        if self.career_goal:
            self.career_goal = self.career_goal.strip().title()
        self.user.save()
        issue = not self.admission_no and bool(self.department_initial) and self.is_student_account
        if issue:
            self.admission_no = self._next_admission_no()
        for attempt in range(5):
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                break
            except IntegrityError:
                # two students were numbered at the same moment: take the next free serial
                if not issue or attempt == 4:
                    raise
                self.admission_no = self._next_admission_no()

    def __str__(self):
        return self.user.username

    @property
    def department_initial(self):
        """First letter of the student's department (Science -> S, Arts -> A, Commercial -> C)."""
        return (self.specialization or '').strip()[:1].upper()

    @property
    def is_student_account(self):
        """Admins/staff and teachers are not students and never get an admission number."""
        u = self.user
        if u.is_staff or u.is_superuser:
            return False
        return not TeacherProfile.objects.filter(user_id=u.pk).exists()

    def _next_admission_no(self):
        """Next serial for this year + department: TMTC/2026/S/001, 002, ... (A for Arts, C for Commercial)."""
        from django.utils import timezone
        year = self.created_at.year if self.created_at else timezone.now().year
        prefix = f'TMTC/{year}/{self.department_initial}/'
        last = 0
        for n in Profile.objects.filter(admission_no__startswith=prefix).values_list('admission_no', flat=True):
            try:
                last = max(last, int(n[len(prefix):]))
            except ValueError:
                pass
        return f'{prefix}{last + 1:03d}'

    @classmethod
    def backfill_admission_numbers(cls):
        """Number existing students who have a department but no admission number yet (oldest first)."""
        for p in cls.objects.filter(admission_no__isnull=True).exclude(specialization='').select_related('user').order_by('created_at', 'pk'):
            p.save()          # save() issues the next serial for students; admins/teachers are skipped

    @property
    def admission_number(self):
        """
        Student admission number, numbered serially per department and year:
        Science TMTC/2026/S/001, 002 ... | Arts TMTC/2026/A/001 ... | Commercial TMTC/2026/C/001 ...
        Issued once (when the student's department is set) and never changes afterwards.
        Admins and teachers have NO admission number: returns ''. A student with no
        department yet has none either, until a department is chosen.
        """
        if not self.is_student_account:
            return ''
        return self.admission_no or ''

    @property
    def get_picture_url(self):
        """
        Always returns a full usable picture URL in both environments.

        Production (USE_CLOUDINARY=True):
          Builds https://res.cloudinary.com/... from the stored public_id.
          Falls back to the default avatar if picture is blank.

        Debug (USE_CLOUDINARY=False):
          Returns the /media/... path via Django storage.
          Falls back to /static/images/male.png if file is missing.
        """
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                pic = self.picture
                # CloudinaryField exposes .public_id; plain string fallback
                public_id = None
                if hasattr(pic, 'public_id') and pic.public_id:
                    public_id = str(pic.public_id).strip()
                elif pic and str(pic).strip() not in ('', 'None'):
                    public_id = str(pic).strip()

                if public_id:
                    return cloudinary.CloudinaryImage(public_id).build_url(secure=True)

                # No picture stored — return the default avatar
                return cloudinary.CloudinaryImage('logo_iowyea').build_url(secure=True)

            else:
                # Debug: standard ImageField
                pic = self.picture
                if pic and hasattr(pic, 'url'):
                    try:
                        url = pic.url
                        if url:
                            return url
                    except Exception:
                        pass
                # Fallback to a static default image
                return static('images/male.png')

        except Exception:
            pass

        return 'https://placehold.co/40x40/dbdbdb/8e8e8e?text=U'


class Post(models.Model):
    post_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_author')
    title = models.CharField(max_length=300)
    about = models.CharField(max_length=400)

    if settings.USE_CLOUDINARY:
        video = CloudinaryField('video', resource_type='video', folder='post_videos', blank=True)
    else:
        video = models.FileField(upload_to='post_videos/')

    subject = models.CharField(max_length=100, blank=True)
    like = models.ManyToManyField(User, related_name='post_like', blank=True)
    view = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def get_video_url(self):
        """
        Always returns a usable video URL in both environments.

        Production (USE_CLOUDINARY=True):
          Builds https://res.cloudinary.com/... from the stored public_id.

        Debug (USE_CLOUDINARY=False):
          Returns the /media/... path via Django storage.
        """
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                vid = self.video
                public_id = None
                if hasattr(vid, 'public_id') and vid.public_id:
                    public_id = str(vid.public_id).strip()
                elif vid and str(vid).strip() not in ('', 'None'):
                    public_id = str(vid).strip()

                if public_id:
                    return cloudinary.CloudinaryVideo(public_id).build_url(secure=True)

            else:
                if self.video and hasattr(self.video, 'url'):
                    try:
                        url = self.video.url
                        if url:
                            return url
                    except Exception:
                        pass

        except Exception:
            pass

        return None


class PostComment(models.Model):
    comment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    commentator = models.ForeignKey(User, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='posts')
    comment = models.TextField()
    like = models.ManyToManyField(User, related_name='comment_likes', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    # null=True so notifications not tied to a post (follow, birthday, etc.) are allowed,
    # and SET_NULL prevents the notification from being wiped when a post is deleted.
    post = models.ForeignKey(Post, on_delete=models.SET_NULL, null=True, blank=True)
    # FK to ClassPost so chat-room reply/like notifications redirect correctly
    class_post = models.ForeignKey(
        'ClassPost', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notifications'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notif → {self.user.username}: {self.message[:50]}"

    # kind -> (label shown on the page, Font Awesome icon). No database column is needed:
    # the kind is worked out from the message text the app writes.
    KIND_META = {
        'exam_scheduled': ('Exam scheduled',   'fa-calendar-check'),
        'exam_done':      ('Exam completed',   'fa-circle-check'),
        'answer':         ('New answer',       'fa-comment-dots'),
        'saved_answer':   ('Saved question',   'fa-bookmark'),
        'accepted':       ('Answer accepted',  'fa-award'),
        'like':           ('New like',         'fa-heart'),
        'report':         ('Report',           'fa-flag'),
        'follow':         ('New follower',     'fa-user-plus'),
        'comment':        ('Comment',          'fa-comment'),
        'announcement':   ('Announcement',     'fa-bullhorn'),
        'general':        ('Notification',     'fa-bell'),
    }

    @property
    def kind(self):
        m = (self.message or '').lower()
        if m.startswith('new announcement'):
            return 'announcement'
        if 'is scheduled for' in m and 'you can start it' in m:
            return 'exam_scheduled'
        if m.startswith('you have completed your'):
            return 'exam_done'
        if 'was reported on' in m:
            return 'report'
        if 'answer was accepted' in m:
            return 'accepted'
        if 'answered your question' in m:
            return 'answer'
        if 'on a question you saved' in m:
            return 'saved_answer'
        if ' liked ' in m:
            return 'like'
        if 'follow' in m:
            return 'follow'
        if any(w in m for w in ('replied', 'comment', 'image', 'audio')):
            return 'comment'
        return 'general'

    @property
    def kind_label(self):
        return self.KIND_META[self.kind][0]

    @property
    def kind_icon(self):
        return self.KIND_META[self.kind][1]

    @property
    def is_exam(self):
        return self.kind in ('exam_scheduled', 'exam_done')


class Announcement(models.Model):
    """
    Posted by an admin or teacher. Who sees it depends on the audience:
      class blank + department blank  -> general (every student)
      class SS1/SS2/SS3               -> that class only
      department Science/Arts/Commercial -> that department only
      both                            -> e.g. only Science SS2 students
    """
    CLASS_CHOICES = [('SS1', 'SS1'), ('SS2', 'SS2'), ('SS3', 'SS3')]

    title = models.CharField(max_length=200)
    message = models.TextField()
    audience_class = models.CharField(max_length=3, blank=True, choices=CLASS_CHOICES)
    audience_dept = models.CharField(max_length=20, blank=True, choices=Profile.SPECIALIZATION_CHOICES)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='announcements')
    created_at = models.DateTimeField(auto_now_add=True)
    # Auto post: students see it (and are notified) from this moment. Default = right away.
    publish_at = models.DateTimeField(default=dj_timezone.now, db_index=True)
    # Auto delete: removed automatically from this moment. Blank = never.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # True once the students were notified. Existing rows default to True (already notified);
    # a scheduled announcement is created with False and notified when its time comes.
    notified = models.BooleanField(default=True)

    class Meta:
        ordering = ['-publish_at', '-created_at']

    def __str__(self):
        return f'{self.title} ({self.audience_label})'

    @property
    def audience_label(self):
        parts = [p for p in (self.audience_dept, self.audience_class) if p]
        return ' '.join(parts) + ' students' if parts else 'Everyone'


# Live Video Models
class LiveSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    room_name = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=300)
    description = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_sessions')
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)  # None = start immediately
    is_active = models.BooleanField(default=True)
    max_participants = models.IntegerField(default=50)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} (by {self.created_by.username})"

    @property
    def host(self):
        """Alias so templates can use {{ session.host }} interchangeably with created_by."""
        return self.created_by

    @property
    def is_scheduled(self):
        """True when the session was created for a future date/time."""
        return self.scheduled_at is not None


class LiveParticipant(models.Model):
    participant_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='live_participations')
    joined_at = models.DateTimeField(auto_now_add=True)
    is_connected = models.BooleanField(default=True)
    is_video_on = models.BooleanField(default=True)
    is_audio_on = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['session', 'user']
    
    def __str__(self):
        return f"{self.user.username} in {self.session.title}"


# ── CBT Models ────────────────────────────────────────────────────

class CBTExam(models.Model):
    """Stores a CBT exam attempt result for a user (one row per subject taken)."""
    # Labels match RegisteredSubject.subject_name (SENIOR_SECONDARY_SUBJECTS in
    # views.py) so a student's registered subjects line up with their results.
    SUBJECT_CHOICES = [
        ('mathematics',   'Mathematics'),
        ('physics',       'Physics'),
        ('english',       'English Language'),
        ('chemistry',     'Chemistry'),
        ('biology',       'Biology'),
        ('government',    'Government'),
        ('computer',      'Computer Studies'),
        ('irk',           'Islamic Religious Studies'),
        ('crk',           'Christian Religious Studies'),
        ('accounting',    'Accounting'),
        ('economics',     'Economics'),
        ('commerce',      'Commerce'),
        ('geography',     'Geography'),
        ('history',       'History'),
        ('literature',    'Literature in English'),
        ('agric',         'Agricultural Science'),
        ('furthermaths',  'Further Mathematics'),
        ('yoruba',        'Yoruba'),
        ('igbo',          'Igbo'),
        ('hausa',         'Hausa'),
        ('french',        'French'),
        ('arabic',        'Arabic'),
        ('civicedu',      'Civic Education'),
        ('techdrawing',   'Technical Drawing'),
        ('homeeconomics', 'Home Economics'),
        ('music',         'Music'),
    ]
    GRADE_CHOICES = [
        ('distinction', 'Distinction'),
        ('credit',      'Credit'),
        ('pass',        'Pass'),
        ('nearly',      'Nearly'),
        ('fail',        'Fail'),
    ]

    EXAM_TYPE_CHOICES = [
        ('practice',    'Test Practice'),
        ('examination', 'CBT Examination'),
    ]

    exam_id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cbt_exams')
    subject       = models.CharField(max_length=30, choices=SUBJECT_CHOICES, default='mathematics')
    score         = models.PositiveIntegerField(default=0)          # raw correct answers
    total         = models.PositiveIntegerField(default=20)         # total questions
    percentage    = models.PositiveIntegerField(default=0)          # 0-100
    grade         = models.CharField(max_length=20, choices=GRADE_CHOICES, default='fail')
    time_used_sec = models.PositiveIntegerField(default=0)          # seconds spent
    # 'practice' = Test Practice (existing rows keep this); 'examination' = the real CBT Examination
    exam_type     = models.CharField(max_length=12, choices=EXAM_TYPE_CHOICES, default='practice')
    taken_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-taken_at']

    def __str__(self):
        return f"{self.student.username} – {self.subject} – {self.score}/{self.total} ({self.percentage}%)"

    @property
    def time_display(self):
        m = self.time_used_sec // 60
        s = self.time_used_sec % 60
        return f"{m:02d}:{s:02d}"


class CBTScore(models.Model):
    """
    Aggregated lifetime CBT stats per user (one row per user).
    Updated every time a new CBTExam is saved.
    """
    user            = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cbt_score')
    total_attempts  = models.PositiveIntegerField(default=0)
    total_correct   = models.PositiveIntegerField(default=0)   # sum of all correct answers
    total_questions = models.PositiveIntegerField(default=0)   # sum of all questions attempted
    best_score      = models.PositiveIntegerField(default=0)   # highest % ever
    points          = models.PositiveIntegerField(default=0)   # gamified points

    def __str__(self):
        return f"{self.user.username} CBT Points: {self.points}"

    def recalculate(self):
        """Recompute all fields from the user's CBTExam history using DB aggregates."""
        from django.db.models import Sum, Max, Count
        exams = CBTExam.objects.filter(student=self.user)
        agg = exams.aggregate(
            count=Count('exam_id'),
            total_correct=Sum('score'),
            total_questions=Sum('total'),
            best_score=Max('percentage'),
        )
        self.total_attempts  = agg['count'] or 0
        self.total_correct   = agg['total_correct'] or 0
        self.total_questions = agg['total_questions'] or 0
        self.best_score      = agg['best_score'] or 0
        # Points: 5 pts per correct answer + 20 bonus for each Distinction
        distinction_count = exams.filter(grade='distinction').count()
        self.points = (self.total_correct * 5) + (distinction_count * 20)
        self.save()


# ── Chat Room / Class Discussion Models ─────────────────────────────

class ClassPost(models.Model):
    """Model for text/image questions posted by students in the chat room"""
    post_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='class_posts')
    title = models.CharField(max_length=300)
    content = models.TextField()

    if settings.USE_CLOUDINARY:
        image = CloudinaryField('image', folder='class_posts', blank=True, null=True)
    else:
        image = models.ImageField(upload_to='class_posts/', blank=True, null=True)

    # Optional voice/audio explanation attached to the question
    # (same storage pattern as ClassPostComment.audio).
    if settings.USE_CLOUDINARY:
        audio = CloudinaryField('audio', resource_type='video', folder='class_post_audio', blank=True, null=True)
    else:
        audio = models.FileField(upload_to='class_post_audio/', blank=True, null=True)

    subject = models.CharField(max_length=100, blank=True, default='General')
    class_level = models.CharField(max_length=10, blank=True, default='')   # 'SS1' / 'SS2' / 'SS3'
    topic = models.CharField(max_length=150, blank=True, default='')
    like = models.ManyToManyField(User, related_name='class_posts_like', blank=True)
    bookmarked_by = models.ManyToManyField(User, related_name='class_posts_bookmarked', blank=True)
    view = models.IntegerField(default=0)
    is_resolved = models.BooleanField(default=False)
    # The one answer the asker (or a teacher) marked as the accepted answer.
    accepted_answer = models.ForeignKey(
        'ClassPostComment', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accepted_for',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title[:50]} by {self.author.username}"

    @property
    def get_image_url(self):
        """Returns a usable image URL in both Cloudinary and local environments."""
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                pic = self.image
                public_id = None
                if hasattr(pic, 'public_id') and pic.public_id:
                    public_id = str(pic.public_id).strip()
                elif pic and str(pic).strip() not in ('', 'None'):
                    public_id = str(pic).strip()
                if public_id:
                    return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
            else:
                if self.image and hasattr(self.image, 'url'):
                    return self.image.url
        except Exception:
            pass
        return None

    @property
    def get_audio_url(self):
        """Returns a usable audio URL in both Cloudinary and local environments."""
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                aud = self.audio
                public_id = None
                if hasattr(aud, 'public_id') and aud.public_id:
                    public_id = str(aud.public_id).strip()
                elif aud and str(aud).strip() not in ('', 'None'):
                    public_id = str(aud).strip()
                if public_id:
                    return cloudinary.CloudinaryVideo(public_id).build_url(secure=True)
            else:
                if self.audio and hasattr(self.audio, 'url'):
                    return self.audio.url
        except Exception:
            pass
        return None

    @property
    def total_comments(self):
        return self.class_comments.count()

    @property
    def total_likes(self):
        return self.like.count()

    @property
    def last_activity(self):
        latest_comment = self.class_comments.order_by('-created_at').first()
        if latest_comment:
            return latest_comment.created_at
        return self.created_at


class ClassPostComment(models.Model):
    """Comments/replies for ClassPost - supports text, images, and audio"""
    comment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    commentator = models.ForeignKey(User, on_delete=models.CASCADE)
    post = models.ForeignKey(ClassPost, on_delete=models.CASCADE, related_name='class_comments')
    comment = models.TextField(blank=True, null=True)  # Made optional since audio/image can be standalone
    
    # Media fields for comments
    if settings.USE_CLOUDINARY:
        image = CloudinaryField('image', folder='comment_images', blank=True, null=True)
        audio = CloudinaryField('audio', resource_type='video', folder='comment_audio', blank=True, null=True)
    else:
        image = models.ImageField(upload_to='comment_images/', blank=True, null=True)
        audio = models.FileField(upload_to='comment_audio/', blank=True, null=True)
    
    like = models.ManyToManyField(User, related_name='class_comment_likes', blank=True)
    # "This answer helped me" votes. Separate from likes: used to rank answers.
    helpful = models.ManyToManyField(User, related_name='class_comment_helpful', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        media_type = []
        if self.comment:
            media_type.append('text')
        if self.image:
            media_type.append('image')
        if self.audio:
            media_type.append('audio')
        media_str = '+'.join(media_type) if media_type else 'empty'
        return f"Comment ({media_str}) by {self.commentator.username} on {self.post.title[:30]}"

    @property
    def total_likes(self):
        return self.like.count()

    @property
    def total_helpful(self):
        return self.helpful.count()
    
    @property
    def get_image_url(self):
        """Returns usable image URL"""
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                img = self.image
                public_id = None
                if hasattr(img, 'public_id') and img.public_id:
                    public_id = str(img.public_id).strip()
                elif img and str(img).strip() not in ('', 'None'):
                    public_id = str(img).strip()
                if public_id:
                    return cloudinary.CloudinaryImage(public_id).build_url(secure=True)
            else:
                if self.image and hasattr(self.image, 'url'):
                    return self.image.url
        except Exception:
            pass
        return None
    
    @property
    def get_audio_url(self):
        """Returns usable audio URL"""
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                aud = self.audio
                public_id = None
                if hasattr(aud, 'public_id') and aud.public_id:
                    public_id = str(aud.public_id).strip()
                elif aud and str(aud).strip() not in ('', 'None'):
                    public_id = str(aud).strip()
                if public_id:
                    return cloudinary.CloudinaryVideo(public_id).build_url(secure=True)
            else:
                if self.audio and hasattr(self.audio, 'url'):
                    return self.audio.url
        except Exception:
            pass
        return None
    
    @property
    def has_media(self):
        return bool(self.image or self.audio)
    
    @property
    def content_summary(self):
        """Returns a summary of the comment content for display in lists"""
        if self.comment:
            return self.comment[:100]
        elif self.image:
            return "📷 Image attachment"
        elif self.audio:
            return "🎵 Audio attachment"
        return "Empty comment"
        
        
        

class ClassPostReport(models.Model):
    """
    A report raised by a user against a question (post) or an answer (comment).
    Exactly one of `post` / `comment` is set. Teachers/admins review open
    reports on the question page and either delete the content or dismiss
    the reports (is_handled=True).
    """
    REASON_CHOICES = [
        ('inappropriate', 'Inappropriate or offensive'),
        ('spam',          'Spam or advertising'),
        ('off_topic',     'Not related to studies'),
        ('incorrect',     'Wrong or misleading'),
        ('other',         'Other'),
    ]

    report_id  = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='class_reports_made')
    post       = models.ForeignKey(ClassPost, on_delete=models.CASCADE, null=True, blank=True, related_name='reports')
    comment    = models.ForeignKey(ClassPostComment, on_delete=models.CASCADE, null=True, blank=True, related_name='reports')
    reason     = models.CharField(max_length=20, choices=REASON_CHOICES)
    details    = models.CharField(max_length=300, blank=True)
    is_handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One report per user per item.
            models.UniqueConstraint(fields=['reporter', 'post'], condition=models.Q(post__isnull=False),
                                    name='uniq_report_user_post'),
            models.UniqueConstraint(fields=['reporter', 'comment'], condition=models.Q(comment__isnull=False),
                                    name='uniq_report_user_comment'),
        ]

    def __str__(self):
        target = 'question' if self.post_id else 'answer'
        return f"{self.reporter.username} reported a {target}: {self.reason}"


# ── Topic Learning Progress ───────────────────────────────────────
# Paste this at the BOTTOM of your existing models.py

class TopicProgress(models.Model):
    """
    Tracks per-user progress on each learn topic.
    One row per (user, subject, topic_key) — upserted when a lesson is completed.
    """
    SUBJECT_CHOICES = [
        ('mathematics', 'Mathematics'),
        ('physics',     'Physics'),
    ]
    STATUS_CHOICES = [
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed',   'Completed'),
    ]

    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='topic_progress')
    subject     = models.CharField(max_length=30, choices=SUBJECT_CHOICES, default='mathematics')
    # topic_key is a short slug e.g. "number-bases", "polynomials", "differentiation"
    topic_key   = models.CharField(max_length=80)
    topic_name  = models.CharField(max_length=200, blank=True)  # display name snapshot
    section     = models.CharField(max_length=100, blank=True)  # e.g. "Number & Numeration"
    status      = models.CharField(max_length=15, choices=STATUS_CHOICES, default='not_started')
    xp_earned   = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'subject', 'topic_key']
        ordering = ['subject', 'section', 'topic_key']

    def __str__(self):
        return f"{self.user.username} – {self.subject} – {self.topic_key} ({self.status})"


# ── Registered Subjects (chosen at registration, Senior Secondary only) ──

class RegisteredSubject(models.Model):
    """
    A subject a student ticked when registering (or later updated), tied to
    the Senior Secondary category.
    """
    CATEGORY_CHOICES = Profile.STUDENT_CATEGORY_CHOICES

    student      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='registered_subjects')
    category     = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    subject_name = models.CharField(max_length=100)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['student', 'subject_name']
        ordering = ['subject_name']

    def __str__(self):
        return f"{self.student.username} – {self.subject_name} ({self.category})"


# ── CBT Examination (real, timed, server-graded test on the student's registered subjects) ──

def _exam_image_field(folder):
    """Optional image, stored the same way as the project's other uploads (Cloudinary or local media)."""
    if settings.USE_CLOUDINARY:
        return CloudinaryField('image', folder=folder, blank=True, null=True)
    return models.ImageField(upload_to=f'{folder}/', blank=True, null=True)


def _exam_image_url(f):
    """Full URL of an exam image in both environments, or '' when there is none."""
    if not f:
        return ''
    try:
        if getattr(settings, 'USE_CLOUDINARY', False):
            import cloudinary
            public_id = getattr(f, 'public_id', None) or str(f)
            return cloudinary.CloudinaryImage(str(public_id)).build_url(secure=True)
        return f.url or ''
    except Exception:
        return ''


# The school's Senior Secondary classes that sit the CBT Examination.
EXAM_CLASS_CHOICES = [('SS1', 'SS1'), ('SS2', 'SS2'), ('SS3', 'SS3')]


class ExamPassage(models.Model):
    """
    Shared information for a group of questions: a comprehension passage, a data table, a map,
    a circuit or any diagram. Students see it above each of its questions with the line
    "Use the information below to answer questions X to Y", and its questions are always given together.
    """
    subject_name = models.CharField(max_length=100, db_index=True)
    # Class this passage is set for. Blank only on passages made before classes existed.
    class_level  = models.CharField(max_length=3, choices=EXAM_CLASS_CHOICES, blank=True, default='', db_index=True)
    # Optional label teachers use in spreadsheets to attach questions to this passage (e.g. P1).
    ref          = models.CharField(max_length=60, blank=True)
    title        = models.CharField(max_length=150, blank=True)
    text         = models.TextField(blank=True)
    image        = _exam_image_field('exam_passages')
    created_by   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')  # teacher who added it
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subject_name', 'class_level', 'id']

    @property
    def image_url(self):
        return _exam_image_url(self.image)

    def __str__(self):
        return f"[{self.subject_name} {self.class_level or 'all'}] {self.title or self.text[:50] or 'Passage %s' % self.pk}"


class ExamQuestion(models.Model):
    """
    One multiple-choice question in the CBT Examination question bank.
    `subject_name` uses the same names as RegisteredSubject.subject_name
    (SENIOR_SECONDARY_SUBJECTS in views.py), so a student's registered
    subjects line up with the questions available to them.
    Teachers add questions from the website: CBT Examination > Manage question bank.
    """
    ANSWER_CHOICES = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')]

    subject_name = models.CharField(max_length=100, db_index=True)
    # Class the teacher set this question for (SS1, SS2 or SS3). Questions saved before classes existed
    # are blank and are given to every class until a teacher assigns them.
    class_level  = models.CharField(max_length=3, choices=EXAM_CLASS_CHOICES, blank=True, default='', db_index=True)
    text         = models.TextField()
    option_a     = models.CharField(max_length=300)
    option_b     = models.CharField(max_length=300)
    option_c     = models.CharField(max_length=300)
    option_d     = models.CharField(max_length=300)
    answer       = models.CharField(max_length=1, choices=ANSWER_CHOICES)
    explanation  = models.TextField(blank=True)
    # Optional diagram / picture for this question.
    image        = _exam_image_field('exam_questions')
    created_by   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')  # teacher who added it
    # Optional shared passage / diagram; `order` keeps the questions of one passage in sequence.
    passage      = models.ForeignKey('ExamPassage', null=True, blank=True, on_delete=models.SET_NULL, related_name='questions')
    order        = models.PositiveIntegerField(default=0)
    is_active    = models.BooleanField(default=True)
    # Hash of subject + question + options: lets the loader re-run safely without duplicating questions.
    fingerprint  = models.CharField(max_length=40, unique=True, editable=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['subject_name', 'class_level', 'id']

    @staticmethod
    def make_fingerprint(subject_name, text, a, b, c, d, class_level=''):
        parts = [subject_name, text, a, b, c, d]
        if class_level:
            # Only added when set, so fingerprints of older (class-less) questions do not change.
            # The same question may therefore exist once for SS1 and once for SS2.
            parts.append(class_level)
        raw = '\x1f'.join(parts).strip().lower()
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

    def save(self, *args, **kwargs):
        self.fingerprint = self.make_fingerprint(
            self.subject_name, self.text, self.option_a, self.option_b, self.option_c, self.option_d,
            self.class_level)
        super().save(*args, **kwargs)

    @property
    def image_url(self):
        return _exam_image_url(self.image)

    def __str__(self):
        return f"[{self.subject_name} {self.class_level or 'all'}] {self.text[:60]}"


class ExamSchedule(models.Model):
    """
    A date and time window set by the admin/teacher during which students of ONE class may start
    the CBT Examination, so each class (SS1 / SS2 / SS3) can be given its own date and time.
    Outside every window the examination is locked.
    A student can sit the exam once per schedule; after that they are told they are done
    until the next schedule opens.
    A blank class_level (schedules made before classes existed) applies to every class.
    """
    title      = models.CharField(max_length=150, blank=True)
    class_level = models.CharField(max_length=3, choices=EXAM_CLASS_CHOICES, blank=True, default='', db_index=True)
    starts_at  = models.DateTimeField()                    # the exam opens
    ends_at    = models.DateTimeField()                    # last moment a student can START the exam
    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='exam_schedules')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_at']

    def __str__(self):
        return f"{self.title or 'CBT Examination'} ({self.class_level or 'all classes'}) – {self.starts_at:%Y-%m-%d %H:%M}"


class ExaminationAttempt(models.Model):
    """
    One sitting of the CBT Examination.

    The paper is frozen at the start: `paper` is a server-side snapshot of the
    questions (with the correct answer), so the student can resume after a
    refresh, later edits to the bank never change a paper in progress, and the
    answers never have to be sent to the browser before the paper is submitted.
    On submission the per-subject scores are also written to CBTExam, so they
    appear on the profile, the printed result slip and the admin dashboard.
    """
    attempt_id     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='examination_attempts')
    # The scheduled exam this sitting belongs to (null for sittings made before scheduling existed).
    schedule       = models.ForeignKey('ExamSchedule', null=True, blank=True, on_delete=models.SET_NULL, related_name='attempts')
    subjects       = models.JSONField(default=list)    # subject names sat, in order
    paper          = models.JSONField(default=list)    # [{id, subject, text, options:[[key,text]..], answer, explanation}]
    answers        = models.JSONField(default=dict)    # {"<question id>": "A".."D"}
    duration_sec   = models.PositiveIntegerField(default=0)
    started_at     = models.DateTimeField(auto_now_add=True)
    submitted_at   = models.DateTimeField(null=True, blank=True)
    is_submitted   = models.BooleanField(default=False)
    auto_submitted = models.BooleanField(default=False)   # True when the timer ran out
    score          = models.PositiveIntegerField(default=0)
    total          = models.PositiveIntegerField(default=0)
    percentage     = models.PositiveIntegerField(default=0)
    grade          = models.CharField(max_length=20, choices=CBTExam.GRADE_CHOICES, default='fail')
    results        = models.JSONField(default=dict)    # {subject: {score, total, answered, percentage, grade}}

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['student', 'is_submitted'])]

    def __str__(self):
        return f"{self.student.username} – exam {self.started_at:%Y-%m-%d %H:%M} ({'submitted' if self.is_submitted else 'in progress'})"

    @property
    def deadline(self):
        from datetime import timedelta
        return self.started_at + timedelta(seconds=self.duration_sec)

    def remaining_sec(self, now=None):
        """Seconds left on the server clock (can be negative once time is up)."""
        from django.utils import timezone
        now = now or timezone.now()
        return int((self.deadline - now).total_seconds())

    @property
    def time_used_display(self):
        if not self.submitted_at:
            return ''
        used = min(self.duration_sec, int((self.submitted_at - self.started_at).total_seconds()))
        return f"{used // 60:02d}:{used % 60:02d}"


# ── Teachers ──────────────────────────────────────────────────────────
# Teachers register themselves (register.html) and are managed by the admin
# (Admin Dashboard > Teachers). Students are registered by the admin only.
# Teachers have NO admission number: that exists only on the student Profile.

class TeacherProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')

    if settings.USE_CLOUDINARY:
        picture = CloudinaryField('picture', folder='teacher_image', default='logo_iowyea')
    else:
        picture = models.ImageField(upload_to='teacher_image/', default='male.png')

    # Personal
    title         = models.CharField(max_length=10, blank=True)               # Mr, Mrs, Dr ...
    gender        = models.CharField(max_length=10, blank=True)
    phone         = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    address       = models.CharField(max_length=200, blank=True)
    location      = models.CharField(max_length=300, blank=True)

    # Qualifications
    qualification        = models.CharField(max_length=80, blank=True)        # highest qualification (e.g. B.Sc / B.Ed)
    course_of_study      = models.CharField(max_length=150, blank=True)       # e.g. Mathematics Education
    institution          = models.CharField(max_length=200, blank=True)       # where they studied
    graduation_year      = models.PositiveSmallIntegerField(null=True, blank=True)
    teaching_certificate = models.CharField(max_length=60, blank=True)        # e.g. TRCN registration number (optional)

    # Teaching
    years_experience = models.PositiveSmallIntegerField(default=0)
    subjects_taught  = models.CharField(max_length=400, blank=True)           # "Mathematics; Physics"
    classes_taught   = models.CharField(max_length=20, blank=True)            # "SS1,SS2"
    bio              = models.CharField(max_length=500, blank=True)

    # Admin approval: a self-registered teacher cannot sign in until approved
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.user.first_name:
            self.user.first_name = self.user.first_name.capitalize()
        if self.user.last_name:
            self.user.last_name = self.user.last_name.capitalize()
        if self.address:
            self.address = self.address.title()
        if self.location:
            self.location = self.location.title()
        if self.institution:
            self.institution = self.institution.title()
        if self.course_of_study:
            self.course_of_study = self.course_of_study.title()
        if self.teaching_certificate:
            self.teaching_certificate = self.teaching_certificate.upper()
        self.user.save()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Teacher: {self.user.username}"

    @property
    def subjects_list(self):
        return [s.strip() for s in self.subjects_taught.split(';') if s.strip()]

    @property
    def classes_list(self):
        return [c.strip() for c in self.classes_taught.split(',') if c.strip()]

    @property
    def display_name(self):
        name = self.user.get_full_name() or self.user.username
        return f'{self.title}. {name}' if self.title else name

    @property
    def get_picture_url(self):
        """Full usable picture URL in both environments (same logic as Profile.get_picture_url)."""
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                pic = self.picture
                public_id = None
                if hasattr(pic, 'public_id') and pic.public_id:
                    public_id = str(pic.public_id).strip()
                elif pic and str(pic).strip() not in ('', 'None'):
                    public_id = str(pic).strip()
                return cloudinary.CloudinaryImage(public_id or 'logo_iowyea').build_url(secure=True)
            pic = self.picture
            if pic and hasattr(pic, 'url'):
                try:
                    if pic.url:
                        return pic.url
                except Exception:
                    pass
            return static('images/male.png')
        except Exception:
            pass
        return 'https://placehold.co/40x40/dbdbdb/8e8e8e?text=T'


# ── Learning materials (notes, past questions, slides, worksheets ...) ──────────
# Teachers / staff upload; students browse, preview and download the ones set for their class.

class LearningMaterial(models.Model):
    """
    A downloadable learning resource. `class_level` blank = for every class.
    The file is stored like the project's other uploads (Cloudinary as a raw file, or local media).
    """
    # extension -> (label, Font Awesome icon, icon colour, preview kind)
    # preview kind is what the detail page can show in the browser; '' = download only.
    TYPE_META = {
        'pdf':  ('PDF document',        'fa-file-pdf',        '#dc2626', 'pdf'),
        'doc':  ('Word document',       'fa-file-word',       '#2563eb', ''),
        'docx': ('Word document',       'fa-file-word',       '#2563eb', ''),
        'ppt':  ('PowerPoint slides',   'fa-file-powerpoint', '#ea580c', ''),
        'pptx': ('PowerPoint slides',   'fa-file-powerpoint', '#ea580c', ''),
        'xls':  ('Excel spreadsheet',   'fa-file-excel',      '#16a34a', ''),
        'xlsx': ('Excel spreadsheet',   'fa-file-excel',      '#16a34a', ''),
        'csv':  ('CSV data',            'fa-file-csv',        '#16a34a', ''),
        'txt':  ('Text file',           'fa-file-lines',      '#525252', 'text'),
        'jpg':  ('Image',               'fa-file-image',      '#9333ea', 'image'),
        'jpeg': ('Image',               'fa-file-image',      '#9333ea', 'image'),
        'png':  ('Image',               'fa-file-image',      '#9333ea', 'image'),
        'mp3':  ('Audio',               'fa-file-audio',      '#0891b2', 'audio'),
        'mp4':  ('Video',               'fa-file-video',      '#0891b2', 'video'),
        'zip':  ('ZIP archive',         'fa-file-zipper',     '#a16207', ''),
    }
    # filter groups shown on the library page: key -> (label, extensions)
    KIND_GROUPS = {
        'pdf':    ('PDF',            ('pdf',)),
        'doc':    ('Word',           ('doc', 'docx')),
        'slides': ('Slides',         ('ppt', 'pptx')),
        'sheet':  ('Spreadsheets',   ('xls', 'xlsx', 'csv')),
        'media':  ('Images & media', ('jpg', 'jpeg', 'png', 'mp3', 'mp4')),
    }

    title        = models.CharField(max_length=200)
    description  = models.CharField(max_length=500, blank=True)
    subject_name = models.CharField(max_length=100, db_index=True)
    # SS1 / SS2 / SS3. Blank = every class.
    class_level  = models.CharField(max_length=3, choices=EXAM_CLASS_CHOICES, blank=True, default='', db_index=True)

    if settings.USE_CLOUDINARY:
        file = CloudinaryField('file', resource_type='raw', folder='learning_materials')
    else:
        file = models.FileField(upload_to='learning_materials/')

    file_name      = models.CharField(max_length=255)                  # original name, used for the download
    file_ext       = models.CharField(max_length=10, db_index=True)    # lower case, no dot
    file_size      = models.PositiveIntegerField(default=0)            # bytes
    uploaded_by    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='learning_materials')
    download_count = models.PositiveIntegerField(default=0)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.subject_name} {self.class_level or 'all'}] {self.title}"

    # ── type information ──
    @property
    def type_label(self):
        return self.TYPE_META.get(self.file_ext, ('File', '', '', ''))[0]

    @property
    def icon(self):
        return self.TYPE_META.get(self.file_ext, ('', 'fa-file', '', ''))[1] or 'fa-file'

    @property
    def icon_color(self):
        return self.TYPE_META.get(self.file_ext, ('', '', '#525252', ''))[2] or '#525252'

    @property
    def preview_kind(self):
        """'pdf' | 'image' | 'video' | 'audio' | 'text' | '' (the browser cannot show this type)."""
        return self.TYPE_META.get(self.file_ext, ('', '', '', ''))[3]

    @property
    def can_preview(self):
        return bool(self.preview_kind)

    @property
    def class_label(self):
        return self.class_level or 'All classes'

    @property
    def size_display(self):
        n = float(self.file_size or 0)
        for unit in ('B', 'KB', 'MB'):
            if n < 1024 or unit == 'MB':
                return f"{int(n)} B" if unit == 'B' else f"{n:.1f} {unit}"
            n /= 1024

    # ── file URLs ──
    def _file_url(self, attachment=False):
        try:
            if getattr(settings, 'USE_CLOUDINARY', False):
                import cloudinary
                f = self.file
                public_id = str(getattr(f, 'public_id', None) or f or '').strip()
                if not public_id or public_id == 'None':
                    return ''
                opts = {'secure': True}
                if attachment:
                    opts['flags'] = 'attachment'
                return cloudinary.CloudinaryResource(public_id, resource_type='raw').build_url(**opts)
            return self.file.url if self.file else ''
        except Exception:
            return ''

    @property
    def file_url(self):
        """URL used for the in-browser preview."""
        return self._file_url()

    @property
    def download_url_direct(self):
        """URL that makes the browser save the file (used by the download view's redirect)."""
        return self._file_url(attachment=True)
