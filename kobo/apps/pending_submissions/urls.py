# coding: utf-8
from django.urls import path

from .views import (
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
]
