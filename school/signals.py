import os

from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def create_default_superuser(sender, **kwargs):
    # post_migrate fires once per app; only run for the school app
    if sender.name != 'school':
        return

    username = os.environ.get('DJANGO_SU_NAME')
    email = os.environ.get('DJANGO_SU_EMAIL')
    password = os.environ.get('DJANGO_SU_PASSWORD')

    # Skip if any variable is missing, so nothing is created with blank credentials
    if not (username and email and password):
        return

    User = get_user_model()
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
