from school.models import Post, Notification

def user_post(request):
    posts = Post.objects.all().order_by('-created_at')
    return{
        'videos':posts
    }

def user_notifications(request):
    if request.user.is_authenticated:
        from school.views import _announcement_tick
        _announcement_tick()      # auto post / auto delete announcements (at most once a minute)
        notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
        unread_count  = notifications.filter(is_read=False).count()
        return {'notifications': notifications, 'unread_count': unread_count}
    return {}