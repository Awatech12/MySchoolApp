# school/templatetags/school_extras.py
from django import template

register = template.Library()

@register.filter
def safe_get(d, key):
    """Safely get a key from a dict; returns empty dict if missing."""
    if isinstance(d, dict):
        return d.get(key, {})
    return {}