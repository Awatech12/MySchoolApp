from django import template

register = template.Library()

@register.filter
def total_media_count(post):
    """Count all media items in a post"""
    count = 0
    if post.file:  # Audio file
        count += 1
    if post.video_file:  # Video file
        count += 1
    count += post.images.count()  # Images
    return count