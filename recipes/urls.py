from django.urls import path
from . import views

app_name = "recipes"
urlpatterns = [
    path("", views.index, name="index"),
    path("save/", views.save_recipe, name="save_recipe"),
    path("saved/", views.saved_recipes, name="saved_recipes"),
    path("saved/<uuid:token>/suggest/", views.suggest_recipe, name="suggest_recipe"),
    path("saved/<uuid:token>/accept/", views.accept_suggestion, name="accept_suggestion"),
    path("saved/<uuid:token>/reject/", views.reject_suggestion, name="reject_suggestion"),
    path("saved/<uuid:token>/undo/", views.undo_suggestion, name="undo_suggestion"),
    path("cook/<uuid:token>/", views.cooking, name="cooking"),
]
