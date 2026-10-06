from django.urls import include, path

from storage.views import index

from . import views

urlpatterns = [
    path("", index),
    path("health/", views.health),
    path("api/storage/", include("storage.urls")),
]
