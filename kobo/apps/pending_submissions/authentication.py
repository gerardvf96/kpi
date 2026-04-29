"""
JWT Token authentication for link-access submissions.

This authentication backend validates JWT tokens stored in cookies
for anonymous users accessing OpenRosa endpoints via Enketo,
when submissions have _editable_via_link or _viewable_via_link set to true.
"""
import logging

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from kpi.models import Asset

logger = logging.getLogger(__name__)


class PendingSubmissionJWTAuthentication(BaseAuthentication):
    """
    Authenticate users via JWT token in cookie for link-access submissions.

    This allows anonymous users with valid JWT tokens to access
    OpenRosa endpoints (formList, manifest, submission) when editing/viewing
    submissions that have _editable_via_link or _viewable_via_link set to true.
    """

    def authenticate(self, request):
        # Get JWT token from cookie or query parameter
        token = (
            request.COOKIES.get('link_access_token')
            or request.GET.get('link_access_token')
        )

        if not token:
            return None

        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=['HS256'],
            )

            submission_id = payload.get('submission_id')
            if not submission_id:
                raise AuthenticationFailed('Invalid token payload')

            asset_uid = payload.get('asset_uid')
            submission = None
            asset = None

            if asset_uid:
                # Fast path: asset_uid is in the JWT
                try:
                    asset = Asset.objects.get(
                        uid=asset_uid, asset_type='survey'
                    )
                    if asset.has_deployment:
                        deployment = asset.deployment
                        submissions = list(deployment.get_submissions(
                            user=asset.owner,
                            query={'meta/rootUuid': submission_id},
                            limit=1,
                        ))
                        if submissions:
                            submission = submissions[0]
                except Asset.DoesNotExist:
                    pass

            if not submission or not asset:
                # Fallback: search all survey assets by rootUuid
                from kpi.models.asset import ASSET_TYPE_SURVEY

                for candidate_asset in Asset.objects.filter(
                    asset_type=ASSET_TYPE_SURVEY,
                    _deployment_data__backend='openrosa',
                ):
                    if not candidate_asset.has_deployment:
                        continue
                    try:
                        submissions = list(
                            candidate_asset.deployment.get_submissions(
                                user=candidate_asset.owner,
                                query={'meta/rootUuid': submission_id},
                                limit=1,
                            )
                        )
                        if submissions:
                            submission = submissions[0]
                            asset = candidate_asset
                            break
                    except Exception:
                        continue

            if not submission or not asset:
                raise AuthenticationFailed('Submission not found')

            # Check that the submission is accessible via link
            editable = str(submission.get('_editable_via_link', '')).lower() == 'true'
            viewable = str(submission.get('_viewable_via_link', '')).lower() == 'true'
            if not editable and not viewable:
                raise AuthenticationFailed(
                    'Submission is not accessible via link'
                )

            auth_dict = {
                'submission_id': submission_id,
                'asset': asset,
                'submission': submission,
                'auth_type': 'link_access_jwt',
            }

            return (asset.owner, auth_dict)

        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('JWT token has expired')
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed(f'Invalid JWT token: {e}')
        except AuthenticationFailed:
            raise
        except Exception as e:
            logger.error(f'Link-access JWT auth error: {e}', exc_info=True)
            raise AuthenticationFailed(f'JWT auth error: {e}')

    def authenticate_header(self, request):
        return 'Bearer realm="api"'
