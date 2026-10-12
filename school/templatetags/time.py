from django import template
from django.utils import timezone
from datetime import timedelta

register = template.Library()

@register.filter
def insta_timesince(d):
    """
    Returns time since d in a concise  (e.g., 1m, 2h, 3d).
    Assumes d is a datetime object in the past.
    """
    now = timezone.now()
    diff = now - d
    
    if diff < timedelta(minutes=1):
        # Seconds are not typically shown
        return "Now"
    elif diff < timedelta(hours=1):
        minutes = int(diff.total_seconds() // 60)
        return f"{minutes}m"
    elif diff < timedelta(days=1):
        hours = int(diff.total_seconds() // 3600)
        return f"{hours}h"
    elif diff < timedelta(days=7):
        days = diff.days
        return f"{days}d"
    elif diff < timedelta(days=30):
        weeks = diff.days // 7
        return f"{weeks}w"
    elif diff < timedelta(days=365):
        months = diff.days // 30
        return f"{months}mo"
    else:
        years = diff.days // 365
        return f"{years}y"
