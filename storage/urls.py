from django.urls import path

from . import views

urlpatterns = [
    path("", views.create_storage),
    path("<str:record_id>/", views.storage_detail),
    path("<str:record_id>/touch/", views.touch_storage),
]
