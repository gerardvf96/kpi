from datetime import datetime, timedelta

import jwt
from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from kpi.models import Asset


class LinkAccessView(APIView):
    """
    Authenticates anonymous link-access to submissions.

    Looks up a submission by rootUuid, checks that _editable_via_link or
    _viewable_via_link is set to 'true', sets a link_access_token JWT cookie,
    and returns the numeric submission _id so the frontend can call the
    standard /enketo/edit or /enketo/view API endpoints.
    """

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request, uid, root_uuid, action):
        if action not in ('edit', 'view'):
            return Response(
                {'detail': 'Invalid action.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            asset = Asset.objects.get(uid=uid, asset_type='survey')
        except Asset.DoesNotExist:
            return Response(
                {'detail': 'Asset not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not asset.has_deployment:
            return Response(
                {'detail': 'Asset is not deployed.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        deployment = asset.deployment

        try:
            submissions = list(deployment.get_submissions(
                user=asset.owner,
                query={'meta/rootUuid': root_uuid},
                limit=1,
            ))
        except Exception:
            submissions = []

        if not submissions:
            return Response(
                {'detail': 'Submission not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        submission = submissions[0]
        flag = '_editable_via_link' if action == 'edit' else '_viewable_via_link'

        if str(submission.get(flag, '')).lower() != 'true':
            return Response(
                {'detail': f'This submission is not {action}able via link.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        submission_id = submission.get('_id')

        # Build JWT cookie so Enketo callbacks can authenticate
        jwt_payload = {
            'type': 'link_access',
            'submission_id': root_uuid,
            'asset_uid': uid,
            'exp': datetime.utcnow() + timedelta(hours=24),
        }
        jwt_token = jwt.encode(
            jwt_payload, settings.SECRET_KEY, algorithm='HS256'
        )

        response = Response(
            {'submission_id': submission_id},
            status=status.HTTP_200_OK,
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
