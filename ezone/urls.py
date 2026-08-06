from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve as serve_static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('auction.urls')),
    path('api/v1/public/teams/', include('teams.urls')),
    path('api/v1/manager/', include('teams.manager_urls')),
    path('api/v1/public/players/', include('players.urls')),
    path('api/v1/manager/', include('players.manager_urls')),
    path('api/v1/assistant/', include('assistant.urls')),
]

# django.conf.urls.static.static() is a no-op whenever DEBUG=False, so it never actually
# serves media in production. Wire the underlying view directly and unconditionally instead.
urlpatterns += [
    re_path(r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'), serve_static, {'document_root': settings.MEDIA_ROOT}),
]
