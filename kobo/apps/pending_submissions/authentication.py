"""
JWT Token authentication for pending submissions.

This authentication backend validates JWT tokens stored in cookies
for anonymous users accessing OpenRosa endpoints via Enketo.
"""
import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from kpi.models import Asset


class PendingSubmissionJWTAuthentication(BaseAuthentication):
    """
    Authenticate users via JWT token in cookie for pending submissions.
    
    This allows anonymous users with valid JWT tokens to access
    OpenRosa endpoints (formList, manifest, submission) when editing
    pending submissions through Enketo.
    """
    
    def authenticate(self, request):
        """
        Attempt to authenticate using JWT token from cookie or query parameter.
        
        Checks both cookie (for browser requests) and query parameter (for Enketo server requests).
        
        Returns:
            tuple: (user, auth_dict) if authentication succeeds
            None: if no JWT token present (allow other auth to proceed)
            
        Raises:
            AuthenticationFailed: if JWT is invalid or submission not accessible
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # DEBUG: Write to file for easy debugging
        import tempfile
        debug_file = tempfile.gettempdir() + '/pending_submission_auth_debug.log'
        try:
            with open(debug_file, 'a') as f:
                f.write(f'\n=== AUTH CHECK: {request.path} ===\n')
                f.write(f'Cookies: {list(request.COOKIES.keys())}\n')
                f.write(f'Has pending_submission_token: {"pending_submission_token" in request.COOKIES}\n')
        except:
            pass  # Ignore file write errors
        
        # Get JWT token from cookie or query parameter
        # Cookie is used for browser requests, query param for Enketo server requests
        token = request.COOKIES.get('pending_submission_token')
        if not token:
            token = request.GET.get('pending_token')
        
        if not token:
            # No JWT token present, let other authentication methods try
            logger.debug(f'PendingSubmissionJWT: No token found in cookies or query params for {request.path}')
            try:
                with open(debug_file, 'a') as f:
                    f.write('Result: NO TOKEN FOUND\n')
            except:
                pass
            return None
        
        logger.info(f'PendingSubmissionJWT: Token found for {request.path}, attempting authentication')
        try:
            with open(debug_file, 'a') as f:
                f.write(f'Result: TOKEN FOUND, attempting auth\n')
        except:
            pass
        
        try:
            # Decode and validate JWT
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=['HS256']
            )
            
            # Extract submission info from token
            submission_id = payload.get('submission_id')
            email = payload.get('email')
            
            if not submission_id or not email:
                raise AuthenticationFailed('Invalid token payload')
            
            # Parse rootUuid from submission_id (format: uuid:xxxxx)
            if not submission_id.startswith('uuid:'):
                raise AuthenticationFailed('Invalid submission ID format')
                
            root_uuid = submission_id
            
            # Find the asset that owns this submission
            # We need to search all survey assets
            from kpi.models.asset import ASSET_TYPE_SURVEY
            
            assets = Asset.objects.filter(
                asset_type=ASSET_TYPE_SURVEY,
                _deployment_data__backend='openrosa'
            )
            
            submission = None
            asset = None
            
            for candidate_asset in assets:
                if not candidate_asset.has_deployment:
                    continue
                    
                deployment = candidate_asset.deployment
                
                # Query for submission by rootUuid
                try:
                    submissions = deployment.get_submissions(
                        user=deployment.asset.owner,
                        query={"meta/rootUuid": root_uuid},
                        limit=1
                    )
                    
                    if submissions:
                        submission = submissions[0]
                        asset = candidate_asset
                        break
                except Exception:
                    continue
            
            if not submission or not asset:
                raise AuthenticationFailed('Submission not found')
            
            # Validate submission status
            submission_status = submission.get('_submission_status')
            if submission_status != 'pending':
                raise AuthenticationFailed('Submission is not pending')
            
            # Validate email is in recipients
            recipients_str = submission.get('_submission_recipients', '')
            recipients = [r.strip() for r in recipients_str.split() if r.strip()]
            
            if email not in recipients:
                raise AuthenticationFailed('Email not authorized for this submission')
            
            # Authentication successful - return asset owner as user
            # and include submission/asset context in auth dict
            user = asset.owner
            logger.info(f'PendingSubmissionJWT: Authentication successful for {email} on asset {asset.uid}')
            
            # DEBUG: Write success to file
            import tempfile
            debug_file = tempfile.gettempdir() + '/pending_submission_auth_debug.log'
            try:
                with open(debug_file, 'a') as f:
                    f.write(f'Result: AUTH SUCCESS for {email} on asset {asset.uid}\n')
            except:
                pass
            
            auth_dict = {
                'submission_id': submission_id,
                'email': email,
                'asset': asset,
                'submission': submission,
                'auth_type': 'pending_submission_jwt'
            }
            
            return (user, auth_dict)
            
        except jwt.ExpiredSignatureError:
            logger.warning('PendingSubmissionJWT: Token has expired')
            import tempfile
            debug_file = tempfile.gettempdir() + '/pending_submission_auth_debug.log'
            try:
                with open(debug_file, 'a') as f:
                    f.write('Result: TOKEN EXPIRED\n')
            except:
                pass
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError as e:
            logger.warning(f'PendingSubmissionJWT: Invalid token - {str(e)}')
            import tempfile
            debug_file = tempfile.gettempdir() + '/pending_submission_auth_debug.log'
            try:
                with open(debug_file, 'a') as f:
                    f.write(f'Result: INVALID TOKEN - {str(e)}\n')
            except:
                pass
            raise AuthenticationFailed('Invalid token')
        except Exception as e:
            # Log the error but don't expose details to client
            logger.error(f'PendingSubmissionJWT: JWT authentication error: {str(e)}', exc_info=True)
            import tempfile
            debug_file = tempfile.gettempdir() + '/pending_submission_auth_debug.log'
            try:
                with open(debug_file, 'a') as f:
                    f.write(f'Result: ERROR - {str(e)}\n')
            except:
                pass
            raise AuthenticationFailed('Authentication failed')
    
    def authenticate_header(self, request):
        """
        Return authentication challenge header.
        """
        return 'Bearer realm="api"'
