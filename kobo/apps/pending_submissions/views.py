# coding: utf-8
from django.conf import settings
from django.template.response import TemplateResponse
from django.utils.translation import gettext as t
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from kpi.utils.mailer import EmailMessage, Mailer

from .models import (
    CODE_EXPIRY_MINUTES,
    MAX_ATTEMPTS,
    PendingSubmissionVerification,
)
from .serializers import (
    SendCodeResponseSerializer,
    SendVerificationCodeSerializer,
    VerificationStatusSerializer,
    VerifyCodeSerializer,
)


class PendingSubmissionPageView(APIView):
    """
    View to render the pending submission verification page.
    
    This is a simple HTML page where anonymous users can enter their email,
    receive a verification code, and verify it.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        return TemplateResponse(
            request,
            'pending_submissions/verify.html',
            {'submission_id': submission_id}
        )


class SendVerificationCodeView(APIView):
    """
    API endpoint to send a verification code to an email address.
    
    This creates a new verification record and sends a code via email.
    """
    
    permission_classes = (AllowAny,)
    
    def post(self, request, submission_id):
        serializer = SendVerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        
        # Create or refresh verification record
        verification = PendingSubmissionVerification.create_or_refresh(
            submission_id=submission_id,
            email=email
        )
        
        # Get the code to send
        code = PendingSubmissionVerification.get_code(submission_id, email)
        
        # Send email with verification code
        success = self._send_verification_email(
            email=email,
            code=code,
            submission_id=submission_id
        )
        
        if success:
            response_data = {
                'success': True,
                'message': t(
                    'A verification code has been sent to your email address. '
                    'The code will expire in %(minutes)d minutes.'
                ) % {'minutes': CODE_EXPIRY_MINUTES}
            }
            return Response(
                SendCodeResponseSerializer(response_data).data,
                status=status.HTTP_200_OK
            )
        else:
            response_data = {
                'success': False,
                'message': t(
                    'Failed to send verification email. Please try again later.'
                )
            }
            return Response(
                SendCodeResponseSerializer(response_data).data,
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _send_verification_email(
        self, email: str, code: str, submission_id: str
    ) -> bool:
        """Send the verification code email."""
        email_message = EmailMessage(
            to=email,
            subject=t('Your verification code for pending submission'),
            plain_text_content_or_template='pending_submissions/emails/verification_code.txt',
            template_variables={
                'code': code,
                'submission_id': submission_id,
                'expiry_minutes': CODE_EXPIRY_MINUTES,
                'base_url': settings.KOBOFORM_URL,
            },
            html_content_or_template='pending_submissions/emails/verification_code.html',
        )
        return Mailer.send(email_message)


class VerifyCodeView(APIView):
    """
    API endpoint to verify a code for a pending submission.
    
    This checks the provided code against the cached verification record.
    """
    
    permission_classes = (AllowAny,)
    
    def post(self, request, submission_id):
        serializer = VerifyCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        code = serializer.validated_data['code']
        
        # Create verification instance and attempt to verify
        verification = PendingSubmissionVerification(submission_id, email)
        success, result = verification.verify(code)
        
        if result == 'no_code_found':
            response_data = {
                'verified': False,
                'message': t(
                    'No verification request found. Please request a new code.'
                )
            }
            return Response(
                VerificationStatusSerializer(response_data).data,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if result == 'max_attempts':
            response_data = {
                'verified': False,
                'message': t(
                    'Maximum verification attempts exceeded. '
                    'Please request a new code.'
                )
            }
            return Response(
                VerificationStatusSerializer(response_data).data,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if success:
            response_data = {
                'verified': True,
                'message': t('Code correct')
            }
            return Response(
                VerificationStatusSerializer(response_data).data,
                status=status.HTTP_200_OK
            )
        else:
            # Extract remaining attempts from result (format: "incorrect:N")
            remaining = int(result.split(':')[1]) if ':' in result else 0
            response_data = {
                'verified': False,
                'message': t(
                    'Code incorrect. You have %(remaining)d attempts remaining.'
                ) % {'remaining': remaining}
            }
            return Response(
                VerificationStatusSerializer(response_data).data,
                status=status.HTTP_400_BAD_REQUEST
            )
