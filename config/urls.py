from django.urls import include, path

from storage.views import index, poc_config

from . import views

urlpatterns = [
    path("", index),
    path("health/", views.health),
    path("api/config/", poc_config),
    path("api/storage/", include("storage.urls")),
]
