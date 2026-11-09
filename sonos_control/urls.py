from django.urls import path

from . import views

urlpatterns = [
    path('', views.sonos_control_view, name='sonos_control'),
    path('state/', views.state, name='sonos_state'),
    path('action/', views.action, name='sonos_action'),
]