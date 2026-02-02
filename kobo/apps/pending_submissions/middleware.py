"""
Middleware to inject JWT authentication for pending submissions.

This middleware adds PendingSubmissionJWTAuthentication to AssetSnapshotViewSet
endpoints when JWT cookies are present, enabling anonymous users to access
OpenRosa endpoints through Enketo.
"""
from django.utils.functional import SimpleLazyObject


class PendingSubmissionAuthMiddleware:
    """
    Middleware to enable JWT authentication for OpenRosa/AssetSnapshot endpoints.
    
    This middleware checks for the pending_submission_token cookie and ensures
    JWT authentication is attempted for AssetSnapshot OpenRosa endpoints that
    Enketo uses (formList, manifest, submission, xform, etc.).
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Check if request has our JWT cookie
        if 'pending_submission_token' in request.COOKIES:
            # Check if this is an AssetSnapshot or OpenRosa endpoint
            path = request.path
            
            # AssetSnapshot endpoints that Enketo uses
            asset_snapshot_patterns = [
                '/api/v2/asset_snapshots/',
                '/formList',
                '/manifest',
                '/submission',
                '/xform',
                '/xml_with_disclaimer',
            ]
            
            # Check if any pattern matches
            if any(pattern in path for pattern in asset_snapshot_patterns):
                # Mark this request as needing JWT authentication
                # The authentication class will check this flag
                request._pending_submission_jwt_enabled = True
        
        response = self.get_response(request)
        return response
