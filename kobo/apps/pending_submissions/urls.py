# coding: utf-8
from django.urls import path

from .views import (
    EnketoEditProxyView,
    PendingSubmissionPageView,
    SendVerificationCodeView,
    VerifyCodeView,
)


urlpatterns = [
    # Main page for pending submission verification
    path(
        '<str:submission_id>/',
        PendingSubmissionPageView.as_view(),
        name='pending-submission-page'
    ),
    # API endpoint to send verification code
    path(
        '<str:submission_id>/send-code/',
        SendVerificationCodeView.as_view(),
        name='pending-submission-send-code'
    ),
    # API endpoint to verify code
    path(
        '<str:submission_id>/verify/',
        VerifyCodeView.as_view(),
        name='pending-submission-verify'
    ),
    # Enketo edit proxy (authenticates with JWT then returns Enketo URL)
    path(
        '<str:submission_id>/enketo/edit/',
        EnketoEditProxyView.as_view(),
        name='pending-submission-enketo-edit'
    ),
    # Enketo edit redirect (authenticates with JWT then redirects to Enketo)
    path(
        '<str:submission_id>/enketo/redirect/edit/',
        EnketoEditProxyView.as_view(),
        name='pending-submission-enketo-redirect-edit'
    ),
]
