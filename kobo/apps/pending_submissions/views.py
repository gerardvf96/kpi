# coding: utf-8
import jwt
import requests
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as t
from rest_framework import status
from rest_framework.pagination import _positive_int as positive_int
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from kobo.apps.openrosa.apps.logger.xform_instance_parser import remove_uuid_prefix
from kpi.authentication import EnketoSessionAuthentication
from kpi.constants import SUBMISSION_FORMAT_TYPE_XML, SUBMISSION_FORMAT_TYPE_JSON
from kpi.models import Asset
from kpi.utils.mailer import EmailMessage, Mailer
from kpi.utils.strings import to_str
from kpi.utils.xml import (
    fromstring_preserve_root_xmlns,
    get_or_create_element,
    xml_tostring,
)

from .models import (
    CODE_EXPIRY_MINUTES,
    MAX_ATTEMPTS,
    PendingSubmissionVerification,
)
from .serializers import (
    SendCodeResponseSerializer,
    SendVerificationCodeSerializer,
    SubmissionInfoSerializer,
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
    
    Before sending the code, it validates:
    1. Submission exists and has _submission_status == 'pending'
    2. Email is in the _submission_recipients field
    """
    
    permission_classes = (AllowAny,)
    
    def post(self, request, submission_id):
        serializer = SendVerificationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        
        # Validate submission and email
        is_valid, error_message, asset = self._validate_submission_and_email(
            submission_id, email
        )
        
        if not is_valid:
            response_data = {
                'success': False,
                'message': error_message
            }
            return Response(
                SendCodeResponseSerializer(response_data).data,
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
            submission_id=submission_id,
            form_name=asset.name if asset else 'Form'
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
    
    def _validate_submission_and_email(
        self, submission_id: str, email: str
    ) -> tuple[bool, str, Asset | None]:
        """
        Validate that:
        1. Submission exists with meta/rootUuid matching submission_id
        2. _submission_status == 'pending'
        3. Email is in _submission_recipients
        
        Returns: (is_valid, error_message, asset)
        """
        try:
            # Find asset by querying for submission with this rootUuid
            # We need to search across all assets since we only have the submission UUID
            from kpi.models import Asset
            
            # Query format: {"meta/rootUuid": "uuid:..."}
            query = {
                "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
            }
            
            # Search for assets that might have this submission
            assets = Asset.objects.filter(asset_type='survey')
            
            submission_data = None
            found_asset = None
            
            for asset in assets:
                try:
                    if not asset.has_deployment:
                        continue
                    deployment = asset.deployment
                    # Use the superuser to bypass permissions for this check
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    admin_user = User.objects.filter(is_superuser=True).first()
                    
                    if admin_user:
                        submissions = list(deployment.get_submissions(
                            user=admin_user,
                            query=query,
                            fields=['_submission_status', '_submission_recipients']
                        ))
                        
                        if submissions:
                            submission_data = submissions[0]
                            found_asset = asset
                            break
                except Exception:
                    continue
            
            if not submission_data:
                return False, t('Submission not found.'), None
            
            # Check if submission status is 'pending'
            submission_status = submission_data.get('_submission_status', '')
            if submission_status != 'pending':
                return False, t('This submission has already been submitted.'), None
            
            # Check if email is in _submission_recipients
            recipients = submission_data.get('_submission_recipients', '')
            if isinstance(recipients, str):
                recipient_emails = recipients.split()
            else:
                recipient_emails = []
            
            if email not in recipient_emails:
                return False, t('This email is not registered for this submission.'), None
            
            return True, '', found_asset
            
        except Exception as e:
            return False, t('Error validating submission: %(error)s') % {'error': str(e)}, None
    
    def _send_verification_email(
        self, email: str, code: str, submission_id: str, form_name: str
    ) -> bool:
        """Send the verification code email."""
        email_message = EmailMessage(
            to=email,
            subject=t('Your verification code for pending submission'),
            plain_text_content_or_template='emails/verification_code.txt',
            template_variables={
                'code': code,
                'submission_id': submission_id,
                'expiry_minutes': CODE_EXPIRY_MINUTES,
                'base_url': settings.KOBOFORM_URL,
            },
            html_content_or_template='emails/verification_code.html',
        )
        return Mailer.send(email_message)


class VerifyCodeView(APIView):
    """
    API endpoint to verify a code for a pending submission.
    
    This checks the provided code against the cached verification record.
    If successful, returns submission info and a JWT access token for Enketo.
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
            # Get submission info and generate access token
            submission_info = self._get_submission_info(submission_id, email)
            access_token = self._generate_access_token(submission_id, email)
            
            response_data = {
                'verified': True,
                'message': t('Code correct'),
                'submission_info': submission_info,
                'access_token': access_token,
            }
            
            # Set JWT as httponly cookie for Enketo authentication
            response = Response(
                VerificationStatusSerializer(response_data).data,
                status=status.HTTP_200_OK
            )
            response.set_cookie(
                'pending_submission_token',
                access_token,
                max_age=3600,  # 1 hour
                httponly=True,
                secure=settings.SECURE_COOKIES if hasattr(settings, 'SECURE_COOKIES') else True,
                samesite='Lax'
            )
            return response
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
    
    def _get_submission_info(self, submission_id: str, email: str) -> dict:
        """
        Retrieve submission information for verified user.
        
        Returns: dict with form_name, last_edit_date, status, enketo_edit_url, asset_uid
        """
        try:
            # Query format
            query = {
                "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
            }
            
            # Search for the submission
            from kpi.models import Asset
            from django.contrib.auth import get_user_model
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            assets = Asset.objects.filter(asset_type='survey')
            
            for asset in assets:
                try:
                    if not asset.has_deployment:
                        continue
                    deployment = asset.deployment
                    if admin_user:
                        submissions = list(deployment.get_submissions(
                            user=admin_user,
                            query=query,
                            fields=['_id', '_submission_status', 'end']
                        ))
                        
                        if submissions:
                            submission_data = submissions[0]
                            last_edit_date = submission_data.get('end', '')
                            submission_status = submission_data.get('_submission_status', 'pending')
                            
                            # Generate Enketo edit URL
                            enketo_edit_url = self._generate_enketo_edit_url(submission_id)
                            
                            return {
                                'form_name': asset.name,
                                'last_edit_date': last_edit_date,
                                'status': submission_status,
                                'enketo_edit_url': enketo_edit_url,
                                'asset_uid': asset.uid,
                            }
                except Exception:
                    continue
            
            # Fallback if submission not found
            return {
                'form_name': 'Unknown',
                'last_edit_date': '',
                'status': 'pending',
                'enketo_edit_url': self._generate_enketo_edit_url(submission_id),
                'asset_uid': '',
            }
        except Exception:
            return {
                'form_name': 'Unknown',
                'last_edit_date': '',
                'status': 'pending',
                'enketo_edit_url': self._generate_enketo_edit_url(submission_id),
                'asset_uid': '',
            }
    
    def _generate_access_token(self, submission_id: str, email: str) -> str:
        """
        Generate a JWT access token for anonymous Enketo access.
        
        Token contains:
        - submission_id: The UUID of the pending submission
        - email: The verified email address
        - exp: Expiration time (1 hour from now)
        - iat: Issued at time
        - type: Token type identifier
        """
        now = datetime.utcnow()
        payload = {
            'submission_id': submission_id,
            'email': email,
            'exp': now + timedelta(hours=1),
            'iat': now,
            'type': 'pending_submission_access'
        }
        
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')
        return token
    
    def _generate_enketo_edit_url(self, submission_id: str) -> str:
        """
        Generate the Enketo edit URL that proxies through our authentication.
        
        This URL points to our proxy endpoint which validates the JWT token
        before redirecting to the actual Enketo edit URL.
        """
        from django.urls import reverse
        base_url = settings.KOBOFORM_URL.rstrip('/')
        
        # Generate URL for our proxy endpoint
        proxy_path = reverse(
            'pending-submission-enketo-edit',
            kwargs={'submission_id': submission_id}
        )
        return f"{base_url}{proxy_path}"


class EnketoEditProxyView(APIView):
    """
    Proxy endpoint for Enketo edit access with JWT authentication.
    
    This view validates the JWT token (from cookie or header) and then
    generates an Enketo edit link for the submission.
    
    For anonymous users editing pending submissions, this provides
    a way to authenticate without platform credentials.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        """
        Handle GET request to edit submission in Enketo.
        
        Validates JWT token and returns/redirects to Enketo edit URL.
        """
        # Extract JWT token from cookie or Authorization header
        token = request.COOKIES.get('pending_submission_token')
        if not token:
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            if auth_header.startswith('Bearer '):
                token = auth_header[7:]
        
        if not token:
            return Response(
                {'error': t('Authentication required. Please verify your email first.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # Validate JWT token
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            
            # Verify token type
            if payload.get('type') != 'pending_submission_access':
                return Response(
                    {'error': t('Invalid token type.')},
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            # Verify submission_id matches
            token_submission_id = payload.get('submission_id', '')
            # Normalize both submission IDs for comparison
            token_sub_id = token_submission_id.replace('uuid:', '')
            current_sub_id = submission_id.replace('uuid:', '')
            
            if token_sub_id != current_sub_id:
                return Response(
                    {'error': t('Token does not match this submission.')},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            email = payload.get('email', '')
            
            # Find the asset and submission
            asset, submission_json = self._find_submission_and_asset(submission_id)
            
            if not asset or not submission_json:
                return Response(
                    {'error': t('Submission not found.')},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Verify email is still in recipients (additional security check)
            recipients = submission_json.get('_submission_recipients', '')
            if isinstance(recipients, str):
                recipient_emails = recipients.split()
            else:
                recipient_emails = []
            
            if email not in recipient_emails:
                return Response(
                    {'error': t('Access denied. Email not authorized for this submission.')},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Verify submission is still pending
            if submission_json.get('_submission_status') != 'pending':
                return Response(
                    {'error': t('This submission is no longer pending and cannot be edited.')},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Generate Enketo edit link
            enketo_response = self._get_enketo_edit_link(
                request, asset, submission_id, submission_json
            )
            
            if enketo_response.status_code == status.HTTP_200_OK:
                # Set CSRF cookie for Enketo authentication
                EnketoSessionAuthentication.prepare_response_with_csrf_cookie(
                    request, enketo_response
                )
                
                # If this is a redirect request, redirect directly to Enketo
                if 'redirect' in request.path:
                    return HttpResponseRedirect(enketo_response.data['url'])
                
                return enketo_response
            else:
                return enketo_response
            
        except jwt.ExpiredSignatureError:
            return Response(
                {'error': t('Token has expired. Please verify your email again.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except jwt.InvalidTokenError:
            return Response(
                {'error': t('Invalid token.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            return Response(
                {'error': t('Error processing request: %(error)s') % {'error': str(e)}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _find_submission_and_asset(self, submission_id: str) -> tuple[Asset | None, dict | None]:
        """
        Find the asset and submission by rootUuid.
        
        Returns: (asset, submission_json) or (None, None) if not found
        """
        try:
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            if not admin_user:
                return None, None
            
            # Query format
            query = {
                "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
            }
            
            # Search for the submission across all deployed assets
            assets = Asset.objects.filter(asset_type='survey')
            
            for asset in assets:
                try:
                    if not asset.has_deployment:
                        continue
                    deployment = asset.deployment
                    submissions = list(deployment.get_submissions(
                        user=admin_user,
                        query=query,
                        format_type=SUBMISSION_FORMAT_TYPE_JSON,
                    ))
                    
                    if submissions:
                        return asset, submissions[0]
                except Exception:
                    continue
            
            return None, None
        except Exception:
            return None, None
    
    def _get_enketo_edit_link(
        self, 
        request, 
        asset: Asset, 
        submission_id: str,
        submission_json: dict
    ) -> Response:
        """
        Generate Enketo edit link for the submission.
        
        Based on the implementation in kpi.views.v2.data.DataViewSet._get_enketo_link
        """
        try:
            deployment = asset.deployment
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            # Get submission ID from JSON (internal _id)
            internal_submission_id = submission_json.get('_id')
            
            if not internal_submission_id:
                return Response(
                    {'error': t('Submission ID not found in submission data.')},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the XML version for Enketo
            submission_xml = deployment.get_submission(
                internal_submission_id, 
                admin_user, 
                SUBMISSION_FORMAT_TYPE_XML
            )
            
            if isinstance(submission_xml, str):
                submission_xml = submission_xml.encode()
            
            submission_xml_root = fromstring_preserve_root_xmlns(submission_xml)
            
            # Add mandatory XML elements if missing
            el = get_or_create_element(
                submission_xml_root, deployment.FORM_UUID_XPATH
            )
            if not el or not el.text.strip():
                form_uuid = deployment.backend_response['uuid']
                el.text = form_uuid
            
            el = get_or_create_element(
                submission_xml_root, deployment.SUBMISSION_CURRENT_UUID_XPATH
            )
            if not el or not el.text.strip():
                el.text = 'uuid:' + submission_json['_uuid']
            
            # Use the latest deployed version
            version_uid = asset.latest_deployed_version.uid
            
            # Get XML root node name from submission
            xml_root_node_name = submission_xml_root.tag
            
            # Create snapshot
            snapshot = asset.snapshot(
                regenerate=True,
                root_node_name=xml_root_node_name,
                version_uid=version_uid,
                submission_uuid=remove_uuid_prefix(submission_json['meta/rootUuid']),
            )
            
            # Prepare data for Enketo API
            data = {
                'server_url': reverse(
                    viewname='assetsnapshot-detail',
                    kwargs={'uid_asset_snapshot': snapshot.uid},
                    request=request,
                ),
                'instance': xml_tostring(submission_xml_root),
                'instance_id': submission_json['_uuid'],
                'form_id': snapshot.uid,
                'return_url': 'false'
            }
            
            # Add attachments if any
            attachments = deployment.get_attachment_objects_from_dict(submission_json)
            for attachment in attachments:
                key_ = f'instance_attachments[{attachment.media_file_basename}]'
                data[key_] = reverse(
                    'attachment-detail',
                    args=(asset.uid, internal_submission_id, attachment.uid),
                    request=request,
                )
            
            # Make request to Enketo API
            response = requests.post(
                f'{settings.ENKETO_URL}/{settings.ENKETO_EDIT_INSTANCE_ENDPOINT}',
                auth=(settings.ENKETO_API_KEY, ''),
                data=data
            )
            
            if response.status_code != status.HTTP_201_CREATED:
                try:
                    parsed_resp = response.json()
                except ValueError:
                    parsed_resp = None
                
                if parsed_resp and 'message' in parsed_resp:
                    message = parsed_resp['message']
                else:
                    message = response.reason
                
                return Response(
                    {'detail': 'Enketo error: ' + message},
                    status=response.status_code,
                )
            
            json_response = response.json()
            enketo_url = json_response.get('edit_url')
            
            return Response(
                {
                    'url': enketo_url,
                    'version_uid': version_uid,
                },
                status=status.HTTP_200_OK
            )
            
        except Exception as e:
            return Response(
                {'error': t('Error generating Enketo link: %(error)s') % {'error': str(e)}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
