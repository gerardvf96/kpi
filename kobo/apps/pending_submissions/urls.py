# coding: utf-8
from django.urls import path

from .views import (
    EnketoEditProxyView,
    EnketoViewProxyView,
    PendingSubmissionPageView,
)


urlpatterns = [
    # Main page for pending submission
    path(
        '<str:submission_id>/',
        PendingSubmissionPageView.as_view(),
        name='pending-submission-page'
    ),
    # Enketo edit proxy (authenticates then returns Enketo URL)
    path(
        '<str:submission_id>/enketo/edit/',
        EnketoEditProxyView.as_view(),
        name='pending-submission-enketo-edit'
    ),
    # Enketo edit redirect (authenticates then redirects to Enketo)
    path(
        '<str:submission_id>/enketo/redirect/edit/',
        EnketoEditProxyView.as_view(),
        name='pending-submission-enketo-redirect-edit'
    ),
    # Enketo view redirect (authenticates then redirects to Enketo view)
    path(
        '<str:submission_id>/enketo/redirect/view/',
        EnketoViewProxyView.as_view(),
        name='pending-submission-enketo-redirect-view'
    ),
]
