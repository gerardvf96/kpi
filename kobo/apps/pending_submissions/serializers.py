# coding: utf-8
from rest_framework import serializers

from .models import PendingSubmissionVerification


class SendVerificationCodeSerializer(serializers.Serializer):
    """Serializer for sending a verification code to an email."""
    
    email = serializers.EmailField(
        required=True,
        help_text='The email address to send the verification code to'
    )


class VerifyCodeSerializer(serializers.Serializer):
    """Serializer for verifying a code."""
    
    email = serializers.EmailField(
        required=True,
        help_text='The email address that received the verification code'
    )
    code = serializers.CharField(
        required=True,
        max_length=10,
        help_text='The verification code received via email'
    )


class SubmissionInfoSerializer(serializers.Serializer):
    """Serializer for submission information after verification."""
    
    form_name = serializers.CharField(
        help_text='Name of the form'
    )
    last_edit_date = serializers.CharField(
        help_text='Last edit date of the submission'
    )
    status = serializers.CharField(
        help_text='Current status of the submission'
    )
    enketo_edit_url = serializers.URLField(
        help_text='URL to edit the submission in Enketo'
    )
    asset_uid = serializers.CharField(
        help_text='UID of the asset (form)'
    )


class VerificationStatusSerializer(serializers.Serializer):
    """Serializer for verification status response."""
    
    verified = serializers.BooleanField(
        help_text='Whether the verification was successful'
    )
    message = serializers.CharField(
        help_text='A human-readable message about the verification status'
    )
    submission_info = SubmissionInfoSerializer(
        required=False,
        help_text='Information about the submission (only if verified)'
    )
    access_token = serializers.CharField(
        required=False,
        help_text='JWT access token for Enketo (only if verified)'
    )


class SendCodeResponseSerializer(serializers.Serializer):
    """Serializer for send code response."""
    
    success = serializers.BooleanField(
        help_text='Whether the code was sent successfully'
    )
    message = serializers.CharField(
        help_text='A human-readable message about the operation'
    )
