# coding: utf-8
import json
import jwt
import requests
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils.translation import gettext as t
from rest_framework import status
from rest_framework.pagination import _positive_int as positive_int
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from kobo.apps.openrosa.apps.logger.xform_instance_parser import remove_uuid_prefix
from kpi.authentication import EnketoSessionAuthentication
from kpi.constants import API_NAMESPACES, SUBMISSION_FORMAT_TYPE_XML, SUBMISSION_FORMAT_TYPE_JSON
from kpi.models import Asset
from kpi.utils.mailer import EmailMessage, Mailer
from kpi.utils.strings import to_str
from kpi.utils.urls import versioned_reverse
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
    AddRecipientSerializer,
    RemoveRecipientSerializer,
    SendCodeResponseSerializer,
    SendVerificationCodeSerializer,
    SubmissionInfoSerializer,
    VerificationStatusSerializer,
    VerifyCodeSerializer,
)


def _generate_enketo_edit_url(submission_id: str) -> str:
    """Generate the Enketo edit URL using our proxy endpoint.
    
    This URL points to our proxy which validates JWT and redirects to Enketo.
    """
    base_url = settings.KOBOFORM_URL.rstrip('/')
    return f"{base_url}/pending-submissions/{submission_id}/enketo/redirect/edit/"


class PendingSubmissionPageView(APIView):
    """
    View to render the pending submission verification page.
    
    This is a simple HTML page where anonymous users can enter their email,
    receive a verification code, and verify it.
    
    If a valid JWT token cookie exists, automatically shows submission data.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        context = {'submission_id': submission_id}
        
        # Check for existing JWT token cookie
        token = request.COOKIES.get('pending_submission_token')
        
        if token:
            try:
                # Decode and validate JWT
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
                token_submission_id = payload.get('submission_id')
                email = payload.get('email')
                
                # Verify token is for this submission
                if token_submission_id == submission_id:
                    # Validate submission still exists and is valid
                    submission_json, asset = self._find_submission(submission_id)
                    
                    if submission_json and asset:
                        # Check email is still valid (allow any status for viewing)
                        submission_status = submission_json.get('_submission_status')
                        recipients = submission_json.get('_submission_recipients', '')
                        recipient_emails = [r.strip() for r in recipients.split() if r.strip()]
                        
                        if email in recipient_emails:
                            # Token is valid! Pre-populate context with submission data
                            submission_info = self._get_submission_info(
                                asset, submission_json, submission_id
                            )
                            context['auto_verified'] = True
                            context['submission_info'] = json.dumps(submission_info)
                            context['email'] = email
            except Exception:
                # Token invalid/expired - just show normal form
                pass
        
        return TemplateResponse(
            request,
            'pending_submissions/verify.html',
            context
        )
    
    def _find_submission(self, submission_id):
        """Find submission by rootUuid across all survey assets"""
        # Use a superuser to query submissions with full permissions
        User = get_user_model()
        superuser = User.objects.filter(is_superuser=True).first()
        
        if not superuser:
            return None, None
        
        for asset in Asset.objects.filter(asset_type='survey'):
            if not hasattr(asset, 'deployment') or not asset.deployment:
                continue
            
            try:
                # Use superuser's permissions to query submissions
                submissions = list(asset.deployment.get_submissions(
                    user=superuser,
                    query={"meta/rootUuid": f"uuid:{submission_id}"},
                    submission_ids=[],
                    limit=1
                ))
                
                if submissions:
                    return submissions[0], asset
            except Exception:
                continue
        
        return None, None
    
    def _get_submission_info(self, asset, submission_json, submission_id):
        """Extract submission info for display"""
        form_name = asset.name
        last_edit_date = submission_json.get('end', submission_json.get('_submission_time', ''))
        submission_status = submission_json.get('_submission_status', '')
        recipients = submission_json.get('_submission_recipients', '')
        
        # Generate Enketo edit URL using shared function
        edit_url = _generate_enketo_edit_url(submission_id)
        
        # Generate Enketo view URL for completed submissions
        base_url = settings.KOBOFORM_URL.rstrip('/')
        view_url = f"{base_url}/pending-submissions/{submission_id}/enketo/redirect/view/"
        
        return {
            'form_name': form_name,
            'last_edit_date': last_edit_date,
            'status': submission_status,
            'recipients': recipients,
            'edit_url': edit_url,
            'view_url': view_url,
        }


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
                secure=False,  # Allow for localhost development
                domain='.kobo.local',  # Accessible across kpi and enketo subdomains
                samesite='Lax'  # Allow same-site and top-level navigation
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
                            fields=['_id', '_submission_status', 'end', '_submission_recipients', 'meta/rootUuid']
                        ))
                        
                        if submissions:
                            submission_data = submissions[0]
                            last_edit_date = submission_data.get('end', '')
                            submission_status = submission_data.get('_submission_status', 'pending')
                            recipients = submission_data.get('_submission_recipients', '')
                            root_uuid = submission_data.get('meta/rootUuid', '')
                            # Extract UUID without 'uuid:' prefix
                            root_uuid_clean = root_uuid.replace('uuid:', '') if root_uuid else submission_id
                            
                            # Generate Enketo edit URL using shared function
                            enketo_edit_url = _generate_enketo_edit_url(root_uuid_clean)
                            
                            # Generate Enketo view URL
                            base_url = settings.KOBOFORM_URL.rstrip('/')
                            enketo_view_url = f"{base_url}/pending-submissions/{root_uuid_clean}/enketo/redirect/view/"
                            
                            return {
                                'form_name': asset.name,
                                'last_edit_date': last_edit_date,
                                'status': submission_status,
                                'recipients': recipients,
                                'enketo_edit_url': enketo_edit_url,
                                'enketo_view_url': enketo_view_url,
                                'asset_uid': asset.uid,
                            }
                except Exception:
                    continue
            
            # Fallback if submission not found
            return {
                'form_name': 'Unknown',
                'last_edit_date': '',
                'status': 'pending',
                'recipients': '',
                'enketo_edit_url': '',
                'asset_uid': '',
            }
        except Exception:
            return {
                'form_name': 'Unknown',
                'last_edit_date': '',
                'status': 'pending',
                'recipients': '',
                'enketo_edit_url': '',
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


class EnketoEditProxyView(APIView):
    """
    Proxy endpoint for Enketo edit access with JWT authentication.
    
    This view validates the JWT token (from cookie or header) and then
    internally calls the Enketo API to get an edit URL and redirects to it.
    
    For anonymous users editing pending submissions, this provides
    a way to authenticate without platform credentials.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        """
        Handle GET request to edit submission in Enketo.
        
        Validates JWT token, calls Enketo API, and redirects to edit URL.
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
            
            # Generate Enketo edit link and redirect immediately (URL expires in 30s)
            enketo_url = self._get_enketo_edit_url(
                request, asset, submission_json, token
            )
            
            if enketo_url:
                return HttpResponseRedirect(enketo_url)
            else:
                return Response(
                    {'error': t('Failed to generate Enketo edit link.')},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
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
    
    def _get_enketo_edit_url(
        self, 
        request, 
        asset: Asset,
        submission_json: dict,
        jwt_token: str
    ) -> str | None:
        """
        Generate Enketo edit URL by calling the Enketo API directly.
        
        This creates a temporary edit URL that expires in 30 seconds.
        The JWT token is appended to server_url so Enketo's server can authenticate.
        """
        try:
            deployment = asset.deployment
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            # Get submission ID from JSON (internal _id)
            internal_submission_id = submission_json.get('_id')
            
            if not internal_submission_id:
                return None
            
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
            
            # Extract submission_id from meta/rootUuid for return URL
            submission_id = remove_uuid_prefix(submission_json['meta/rootUuid'])
            return_url = request.build_absolute_uri(
                f'/pending-submissions/{submission_id}/'
            )
            
            # Prepare data for Enketo API
            data = {
                'server_url': versioned_reverse(
                    viewname='assetsnapshot-detail',
                    kwargs={'uid_asset_snapshot': snapshot.uid},
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
                ),
                'instance': xml_tostring(submission_xml_root),
                'instance_id': submission_json['_uuid'],
                'form_id': snapshot.uid,
                'return_url': return_url
            }
            
            # Add attachments if any
            attachments = deployment.get_attachment_objects_from_dict(submission_json)
            for attachment in attachments:
                key_ = f'instance_attachments[{attachment.media_file_basename}]'
                data[key_] = versioned_reverse(
                    viewname='attachment-detail',
                    args=(asset.uid, internal_submission_id, attachment.uid),
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
                )
            
            # Make request to Enketo API
            response = requests.post(
                f'{settings.ENKETO_URL}/{settings.ENKETO_EDIT_INSTANCE_ENDPOINT}',
                auth=(settings.ENKETO_API_KEY, ''),
                data=data
            )
            
            if response.status_code != status.HTTP_201_CREATED:
                return None
            
            json_response = response.json()
            return json_response.get('edit_url')
            
        except Exception:
            return None
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
                    enketo_url = enketo_response.data['url']
                    
                    # Forward any query parameters from the original request to Enketo
                    query_params = request.GET.dict()
                    if query_params:
                        from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
                        parsed_url = urlparse(enketo_url)
                        existing_params = parse_qs(parsed_url.query)
                        # Merge existing params with new ones (new ones take priority)
                        for key, value in query_params.items():
                            existing_params[key] = [value]
                        # Flatten the params back to regular dict
                        merged_params = {k: v[0] for k, v in existing_params.items()}
                        new_query = urlencode(merged_params)
                        new_url = urlunparse((
                            parsed_url.scheme,
                            parsed_url.netloc,
                            parsed_url.path,
                            parsed_url.params,
                            new_query,
                            parsed_url.fragment
                        ))
                        return HttpResponseRedirect(new_url)
                    
                    return HttpResponseRedirect(enketo_url)
                
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
            
            # Extract submission_id from meta/rootUuid for return URL
            submission_id = remove_uuid_prefix(submission_json['meta/rootUuid'])
            return_url = request.build_absolute_uri(
                f'/pending-submissions/{submission_id}/'
            )
            
            # Prepare data for Enketo API
            data = {
                'server_url': versioned_reverse(
                    viewname='assetsnapshot-detail',
                    kwargs={'uid_asset_snapshot': snapshot.uid},
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
                ),
                'instance': xml_tostring(submission_xml_root),
                'instance_id': submission_json['_uuid'],
                'form_id': snapshot.uid,
                'return_url': return_url
            }
            
            # Add attachments if any
            attachments = deployment.get_attachment_objects_from_dict(submission_json)
            for attachment in attachments:
                key_ = f'instance_attachments[{attachment.media_file_basename}]'
                data[key_] = versioned_reverse(
                    viewname='attachment-detail',
                    args=(asset.uid, internal_submission_id, attachment.uid),
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
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


class EnketoViewProxyView(APIView):
    """
    Proxy endpoint for Enketo view access with JWT authentication.
    
    Similar to EnketoEditProxyView but generates a view-only link for completed submissions.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        """
        Handle GET request to view submission in Enketo.
        
        Validates JWT token and redirects to Enketo view URL.
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
            token_submission_id = payload.get('submission_id', '').replace('uuid:', '')
            current_sub_id = submission_id.replace('uuid:', '')
            
            if token_submission_id != current_sub_id:
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
            
            # Verify email is still in recipients
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
            
            # Generate Enketo view link (works for any status)
            enketo_url = self._get_enketo_view_url(request, asset, submission_json)
            
            if enketo_url:
                return HttpResponseRedirect(enketo_url)
            else:
                return Response(
                    {'error': t('Failed to generate Enketo view link.')},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
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
        """Find the asset and submission by rootUuid."""
        try:
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            if not admin_user:
                return None, None
            
            query = {
                "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
            }
            
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
    
    def _get_enketo_view_url(self, request, asset: Asset, submission_json: dict) -> str | None:
        """Generate Enketo view URL by calling the Enketo API."""
        try:
            deployment = asset.deployment
            User = get_user_model()
            admin_user = User.objects.filter(is_superuser=True).first()
            
            internal_submission_id = submission_json.get('_id')
            
            if not internal_submission_id:
                return None
            
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
            
            version_uid = asset.latest_deployed_version.uid
            xml_root_node_name = submission_xml_root.tag
            
            # Create snapshot
            snapshot = asset.snapshot(
                regenerate=True,
                root_node_name=xml_root_node_name,
                version_uid=version_uid,
                submission_uuid=remove_uuid_prefix(submission_json['meta/rootUuid']),
            )
            
            # Extract submission_id for return URL
            submission_id = remove_uuid_prefix(submission_json['meta/rootUuid'])
            return_url = request.build_absolute_uri(
                f'/pending-submissions/{submission_id}/'
            )
            
            # Prepare data for Enketo VIEW API (not edit)
            data = {
                'server_url': versioned_reverse(
                    viewname='assetsnapshot-detail',
                    kwargs={'uid_asset_snapshot': snapshot.uid},
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
                ),
                'instance': xml_tostring(submission_xml_root),
                'instance_id': submission_json['_uuid'],
                'form_id': snapshot.uid,
                'return_url': return_url
            }
            
            # Add attachments if any
            attachments = deployment.get_attachment_objects_from_dict(submission_json)
            for attachment in attachments:
                key_ = f'instance_attachments[{attachment.media_file_basename}]'
                data[key_] = versioned_reverse(
                    viewname='attachment-detail',
                    args=(asset.uid, internal_submission_id, attachment.uid),
                    request=request,
                    url_namespace=API_NAMESPACES['default'],
                )
            
            # Make request to Enketo VIEW INSTANCE API endpoint
            response = requests.post(
                f'{settings.ENKETO_URL}/{settings.ENKETO_VIEW_INSTANCE_ENDPOINT}',
                auth=(settings.ENKETO_API_KEY, ''),
                data=data
            )
            
            if response.status_code != status.HTTP_201_CREATED:
                return None
            
            json_response = response.json()
            return json_response.get('view_url')
            
        except Exception:
            return None


class AddRecipientView(APIView):
    """
    API endpoint to add a recipient to a pending submission.
    
    Requires JWT authentication. Adds the email to _submission_recipients.
    """
    
    permission_classes = (AllowAny,)
    
    def post(self, request, submission_id):
        """Add a recipient email to the submission."""
        # Validate JWT token
        token = request.COOKIES.get('pending_submission_token')
        if not token:
            return Response(
                {'error': t('Authentication required.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            token_submission_id = payload.get('submission_id', '').replace('uuid:', '')
            current_sub_id = submission_id.replace('uuid:', '')
            
            if token_submission_id != current_sub_id:
                return Response(
                    {'error': t('Token does not match this submission.')},
                    status=status.HTTP_403_FORBIDDEN
                )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return Response(
                {'error': t('Invalid or expired token.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # Validate request data
        serializer = AddRecipientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_email = serializer.validated_data['email']
        
        # Find submission and asset
        submission_json, asset = self._find_submission(submission_id)
        
        if not submission_json or not asset:
            return Response(
                {'error': t('Submission not found.')},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get current recipients
        recipients = submission_json.get('_submission_recipients', '')
        recipient_list = [r.strip() for r in recipients.split() if r.strip()]
        
        # Check if email already exists
        if new_email in recipient_list:
            return Response(
                {'error': t('Email is already a recipient.')},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Add new email
        recipient_list.append(new_email)
        updated_recipients = ' '.join(recipient_list)
        
        # Update submission
        success, error_detail = self._update_submission_recipients(
            asset, submission_json, updated_recipients
        )
        
        if success:
            return Response(
                {
                    'success': True,
                    'message': t('Recipient added successfully.'),
                    'recipients': updated_recipients
                },
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {'error': t('Failed to update submission: %(detail)s') % {'detail': error_detail}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _find_submission(self, submission_id):
        """Find submission by rootUuid."""
        User = get_user_model()
        superuser = User.objects.filter(is_superuser=True).first()
        
        if not superuser:
            return None, None
        
        query = {
            "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
        }
        
        for asset in Asset.objects.filter(asset_type='survey'):
            if not hasattr(asset, 'deployment') or not asset.deployment:
                continue
            
            try:
                submissions = list(asset.deployment.get_submissions(
                    user=superuser,
                    query=query,
                    submission_ids=[],
                    limit=1
                ))
                
                if submissions:
                    return submissions[0], asset
            except Exception:
                continue
        
        return None, None
    
    def _update_submission_recipients(self, asset, submission_json, new_recipients):
        """Update the _submission_recipients field in the submission."""
        try:
            User = get_user_model()
            superuser = User.objects.filter(is_superuser=True).first()
            
            if not superuser:
                return False, 'No superuser found'
            
            internal_submission_id = submission_json.get('_id')
            
            if not internal_submission_id:
                return False, 'No submission _id found'
            
            # Get or create API token for superuser
            from rest_framework.authtoken.models import Token
            token, created = Token.objects.get_or_create(user=superuser)
            
            # Call the public bulk update API endpoint
            bulk_update_url = f"{settings.KOBOFORM_URL}/api/v2/assets/{asset.uid}/data/bulk/"
            
            payload = {
                'payload': {
                    'submission_ids': [internal_submission_id],
                    'data': {
                        '_submission_recipients': new_recipients
                    }
                }
            }
            
            response = requests.patch(
                bulk_update_url,
                json=payload,
                headers={
                    'Authorization': f'Token {token.key}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return True, None
            else:
                return False, f"HTTP {response.status_code}: {response.text}"
                
        except Exception as e:
            return False, f"Exception: {str(e)}"


class RemoveRecipientView(APIView):
    """
    API endpoint to remove a recipient from a pending submission.
    
    Requires JWT authentication. Removes the email from _submission_recipients.
    """
    
    permission_classes = (AllowAny,)
    
    def post(self, request, submission_id):
        """Remove a recipient email from the submission."""
        # Validate JWT token
        token = request.COOKIES.get('pending_submission_token')
        if not token:
            return Response(
                {'error': t('Authentication required.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            token_submission_id = payload.get('submission_id', '').replace('uuid:', '')
            current_sub_id = submission_id.replace('uuid:', '')
            
            if token_submission_id != current_sub_id:
                return Response(
                    {'error': t('Token does not match this submission.')},
                    status=status.HTTP_403_FORBIDDEN
                )
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return Response(
                {'error': t('Invalid or expired token.')},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # Validate request data
        serializer = RemoveRecipientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email_to_remove = serializer.validated_data['email']
        
        # Find submission and asset
        submission_json, asset = self._find_submission(submission_id)
        
        if not submission_json or not asset:
            return Response(
                {'error': t('Submission not found.')},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get current recipients
        recipients = submission_json.get('_submission_recipients', '')
        recipient_list = [r.strip() for r in recipients.split() if r.strip()]
        
        # Check if email exists
        if email_to_remove not in recipient_list:
            return Response(
                {'error': t('Email is not a recipient.')},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Remove email
        recipient_list.remove(email_to_remove)
        updated_recipients = ' '.join(recipient_list)
        
        # Update submission
        success, error_detail = self._update_submission_recipients(
            asset, submission_json, updated_recipients
        )
        
        if success:
            return Response(
                {
                    'success': True,
                    'message': t('Recipient removed successfully.'),
                    'recipients': updated_recipients
                },
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {'error': t('Failed to update submission: %(detail)s') % {'detail': error_detail}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _find_submission(self, submission_id):
        """Find submission by rootUuid."""
        User = get_user_model()
        superuser = User.objects.filter(is_superuser=True).first()
        
        if not superuser:
            return None, None
        
        query = {
            "meta/rootUuid": f"uuid:{submission_id}" if not submission_id.startswith('uuid:') else submission_id
        }
        
        for asset in Asset.objects.filter(asset_type='survey'):
            if not hasattr(asset, 'deployment') or not asset.deployment:
                continue
            
            try:
                submissions = list(asset.deployment.get_submissions(
                    user=superuser,
                    query=query,
                    submission_ids=[],
                    limit=1
                ))
                
                if submissions:
                    return submissions[0], asset
            except Exception:
                continue
        
        return None, None
    
    def _update_submission_recipients(self, asset, submission_json, new_recipients):
        """Update the _submission_recipients field in the submission."""
        try:
            User = get_user_model()
            superuser = User.objects.filter(is_superuser=True).first()
            
            if not superuser:
                return False, 'No superuser found'
            
            internal_submission_id = submission_json.get('_id')
            
            if not internal_submission_id:
                return False, 'No submission _id found'
            
            # Get or create API token for superuser
            from rest_framework.authtoken.models import Token
            token, created = Token.objects.get_or_create(user=superuser)
            
            # Call the public bulk update API endpoint
            bulk_update_url = f"{settings.KOBOFORM_URL}/api/v2/assets/{asset.uid}/data/bulk/"
            
            payload = {
                'payload': {
                    'submission_ids': [internal_submission_id],
                    'data': {
                        '_submission_recipients': new_recipients
                    }
                }
            }
            
            response = requests.patch(
                bulk_update_url,
                json=payload,
                headers={
                    'Authorization': f'Token {token.key}',
                    'Content-Type': 'application/json'
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return True, None
            else:
                return False, f"HTTP {response.status_code}: {response.text}"
                
        except Exception as e:
            return False, f"Exception: {str(e)}"
