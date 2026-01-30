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


class VerificationStatusSerializer(serializers.Serializer):
    """Serializer for verification status response."""
    
    verified = serializers.BooleanField(
        help_text='Whether the verification was successful'
    )
    message = serializers.CharField(
        help_text='A human-readable message about the verification status'
    )


class SendCodeResponseSerializer(serializers.Serializer):
    """Serializer for send code response."""
    
    success = serializers.BooleanField(
        help_text='Whether the code was sent successfully'
    )
    message = serializers.CharField(
        help_text='A human-readable message about the operation'
    )
