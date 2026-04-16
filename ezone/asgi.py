import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ezone.settings')

# Initialize Django ASGI app early to populate app registry
django_asgi_app = get_asgi_application()

from auction import routing  # noqa: E402
from ezone.middleware import TokenAuthMiddleware # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': TokenAuthMiddleware(
        URLRouter(routing.websocket_urlpatterns)
    ),
})
