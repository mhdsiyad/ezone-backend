from django.urls import re_path
from .consumers import AuctionConsumer

websocket_urlpatterns = [
    re_path(r'^ws/auction/(?P<auction_id>[^/]+)/$', AuctionConsumer.as_asgi()),
]
