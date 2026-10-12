from django.contrib import admin
from school.models import Profile, Post, PostComment, Notification

# Register your models here.
admin.site.register(Profile)
admin.site.register(Post)
admin.site.register(PostComment)
admin.site.register(Notification)