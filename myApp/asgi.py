import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from school import route

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myApp.settings')

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(
        URLRouter(
           route.websocket_urlpatterns
        )
    ),
})