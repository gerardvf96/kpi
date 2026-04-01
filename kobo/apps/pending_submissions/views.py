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
    View to render the pending submission page.
    
    Displays submission information and edit/view buttons for pending submissions.
    No email verification required.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        context = {'submission_id': submission_id}
        
        # Find the submission
        submission_json, asset = self._find_submission(submission_id)
        
        if not submission_json or not asset:
            context['error'] = 'Submission not found.'
            return TemplateResponse(
                request,
                'pending_submissions/verify.html',
                context
            )
        
        # Check if submission is pending
        submission_status = submission_json.get('_submission_status')
        
        if submission_status != 'pending':
            context['error'] = f'This submission has status "{submission_status}" and cannot be edited.'
            return TemplateResponse(
                request,
                'pending_submissions/verify.html',
                context
            )
        
        # Get submission info and pass to template
        submission_info = self._get_submission_info(
            asset, submission_json, submission_id
        )
        context['submission_info'] = json.dumps(submission_info)
        
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


class EnketoEditProxyView(APIView):
    """
    Proxy endpoint for Enketo edit access without authentication.
    
    This view generates an Enketo edit link for pending submissions.
    No authentication required - access is based on submission status.
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        """
        Handle GET request to edit submission in Enketo.
        
        Validates submission is pending, calls Enketo API, and redirects to edit URL.
        """
        # Find the asset and submission
        asset, submission_json = self._find_submission_and_asset(submission_id)
        
        if not asset or not submission_json:
            return Response(
                {'error': t('Submission not found.')},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify submission is pending
        if submission_json.get('_submission_status') != 'pending':
            return Response(
                {'error': t('This submission is no longer pending and cannot be edited.')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Generate Enketo edit link and redirect immediately (URL expires in 30s)
        enketo_url = self._get_enketo_edit_url(
            request, asset, submission_json
        )
        
        if enketo_url:
            # Add lang=ca parameter to Enketo URL
            from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
            parsed_url = urlparse(enketo_url)
            existing_params = parse_qs(parsed_url.query)
            existing_params['lang'] = ['ca']
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
        else:
            return Response(
                {'error': t('Failed to generate Enketo edit link.')},
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
        submission_json: dict
    ) -> str | None:
        """
        Generate Enketo edit URL by calling the Enketo API directly.
        
        This creates a temporary edit URL that expires in 30 seconds.
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
            
            # Ensure __version__ element exists and set its value
            el = get_or_create_element(submission_xml_root, '__version__')
            el.text = version_uid
            
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
                    # Always add lang=ca parameter
                    from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
                    parsed_url = urlparse(enketo_url)
                    existing_params = parse_qs(parsed_url.query)
                    
                    # Add query params from request
                    query_params = request.GET.dict()
                    for key, value in query_params.items():
                        existing_params[key] = [value]
                    
                    # Always add lang=ca
                    existing_params['lang'] = ['ca']
                    
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
            
            # Ensure __version__ element exists and set its value
            el = get_or_create_element(submission_xml_root, '__version__')
            el.text = version_uid
            
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
    Proxy endpoint for Enketo view access without authentication.
    
    Generates a view-only link for submissions (any status).
    """
    
    permission_classes = (AllowAny,)
    
    def get(self, request, submission_id):
        """
        Handle GET request to view submission in Enketo.
        
        Redirects to Enketo view URL.
        """
        # Find the asset and submission
        asset, submission_json = self._find_submission_and_asset(submission_id)
        
        if not asset or not submission_json:
            return Response(
                {'error': t('Submission not found.')},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Generate Enketo view link (works for any status)
        enketo_url = self._get_enketo_view_url(request, asset, submission_json)
        
        if enketo_url:
            # Add lang=ca parameter to Enketo URL
            from urllib.parse import urlencode, urlparse, urlunparse, parse_qs
            parsed_url = urlparse(enketo_url)
            existing_params = parse_qs(parsed_url.query)
            existing_params['lang'] = ['ca']
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
        else:
            return Response(
                {'error': t('Failed to generate Enketo view link.')},
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
            
            # Ensure __version__ element exists and set its value
            el = get_or_create_element(submission_xml_root, '__version__')
            el.text = version_uid
            
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
