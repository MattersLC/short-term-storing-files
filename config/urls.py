from django.urls import include, path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("api/storage/", include("storage.urls")),
]
