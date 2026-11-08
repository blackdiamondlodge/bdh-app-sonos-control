from django.urls import path
from . import views

urlpatterns = [
    path('', views.sonos_control_view, name='sonos_control'),
    path('toggle_group/', views.toggle_group, name='toggle_group'),
    path('adjust-volume/', views.adjust_volume, name='adjust_volume'),
    path('toggle-play-pause/', views.toggle_play_pause, name='toggle_play_pause'),
    path('play-track/', views.play_track, name='play_track'),
    path('play-uri/', views.play_uri, name='play_uri'),
    path('queue-track/', views.queue_track, name='queue_track'),
    path('search/', views.spotify_search, name='spotify_search'),
    #path('spotify/login/', views.spotify_login, name='spotify_login'),
    path('spotify/auth/qrcode/', views.spotify_auth_qrcode, name='spotify_auth_qrcode'),
    path('spotify/auth/callback/', views.spotify_callback, name='spotify_callback'),
    path('spotify/auth/status', views.spotify_auth_status, name='spotify_auth_status'),
    path('spotify/', views.spotify_home, name='spotify_home'),
    path('spotify/data/', views.fetch_spotify_data, name='fetch_spotify_data'),
    path('spotify/logout/', views.spotify_logout, name='spotify_logout'),
]
