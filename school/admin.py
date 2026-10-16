from django.contrib import admin
from school.models import Profile, Post, PostComment, Notification, ExamQuestion, ExamPassage, ExaminationAttempt, ExamSchedule

# Register your models here.
admin.site.register(Profile)
admin.site.register(Post)
admin.site.register(PostComment)
admin.site.register(Notification)


@admin.register(ExamQuestion)
class ExamQuestionAdmin(admin.ModelAdmin):
    list_display = ('subject_name', 'short_text', 'answer', 'is_active')
    list_filter = ('subject_name', 'is_active', 'answer')
    search_fields = ('text',)
    list_editable = ('is_active',)

    @admin.display(description='Question')
    def short_text(self, obj):
        return obj.text[:90]


@admin.register(ExamPassage)
class ExamPassageAdmin(admin.ModelAdmin):
    list_display = ('subject_name', 'ref', 'title')
    list_filter = ('subject_name',)
    search_fields = ('title', 'text', 'ref')


@admin.register(ExamSchedule)
class ExamScheduleAdmin(admin.ModelAdmin):
    list_display = ('title', 'starts_at', 'ends_at', 'is_active')
    list_filter = ('is_active',)


@admin.register(ExaminationAttempt)
class ExaminationAttemptAdmin(admin.ModelAdmin):
    list_display = ('student', 'started_at', 'is_submitted', 'auto_submitted', 'score', 'total', 'percentage', 'grade')
    list_filter = ('is_submitted', 'auto_submitted', 'grade')
    search_fields = ('student__username', 'student__first_name', 'student__last_name')
    readonly_fields = ('paper', 'answers', 'results')
