from school.models import Post, Notification

def user_post(request):
    posts = Post.objects.all().order_by('-created_at')
    return{
        'videos':posts
    }

def user_notifications(request):
    if request.user.is_authenticated:
        notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
        unread_count  = notifications.filter(is_read=False).count()
        return {'notifications': notifications, 'unread_count': unread_count}
    return {}