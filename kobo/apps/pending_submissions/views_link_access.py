from datetime import datetime, timedelta

import jwt
from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseNotFound, HttpResponseRedirect
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from kpi.models import Asset


class LinkAccessView(APIView):
    """
    Sets a link_access_token cookie and redirects to the Enketo
    redirect endpoint for anonymous edit/view of submissions that
    have _editable_via_link or _viewable_via_link set to 'true'.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request, uid, submission_id, action):
        if action not in ('edit', 'view'):
            return HttpResponseNotFound()

        try:
            asset = Asset.objects.get(uid=uid, asset_type='survey')
        except Asset.DoesNotExist:
            return HttpResponseNotFound('Asset not found.')

        if not asset.has_deployment:
            return HttpResponseNotFound('Asset is not deployed.')

        deployment = asset.deployment

        try:
            submissions = list(deployment.get_submissions(
                user=asset.owner,
                query={'_id': int(submission_id)},
                limit=1,
            ))
        except Exception:
            return HttpResponseNotFound('Submission not found.')

        if not submissions:
            return HttpResponseNotFound('Submission not found.')

        submission = submissions[0]
        flag = '_editable_via_link' if action == 'edit' else '_viewable_via_link'

        if submission.get(flag) != 'true':
            return HttpResponseForbidden(
                f'This submission is not {action}able via link.'
            )

        # Build redirect URL to existing enketo redirect endpoint
        redirect_url = (
            f'/api/v2/assets/{uid}/data/{submission_id}'
            f'/enketo/redirect/{action}/'
        )

        response = HttpResponseRedirect(redirect_url)

        # Set JWT cookie so Enketo callbacks can authenticate
        root_uuid = submission.get('meta/rootUuid', '')
        jwt_payload = {
            'type': 'link_access',
            'submission_id': root_uuid,
            'asset_uid': uid,
            'exp': datetime.utcnow() + timedelta(hours=24),
        }
        jwt_token = jwt.encode(
            jwt_payload, settings.SECRET_KEY, algorithm='HS256'
        )
        response.set_cookie(
            key='link_access_token',
            value=jwt_token,
            domain=settings.SESSION_COOKIE_DOMAIN,
            secure=settings.SESSION_COOKIE_SECURE or None,
            httponly=True,
            samesite='Lax',
        )

        return response
