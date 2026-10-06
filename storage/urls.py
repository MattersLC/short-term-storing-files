from django.urls import path

from . import views

urlpatterns = [
    path("", views.create_storage),
    path("<str:record_id>/", views.get_storage),
]
