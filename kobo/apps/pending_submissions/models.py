# coding: utf-8
from __future__ import annotations

import hashlib
import secrets
import string
from dataclasses import dataclass
from typing import Optional

from django.core.cache import cache


def generate_verification_code(length: int = 6) -> str:
    """Generate a random numeric verification code."""
    return ''.join(secrets.choice(string.digits) for _ in range(length))


# Configurable constants
CODE_EXPIRY_MINUTES = 15
MAX_ATTEMPTS = 5


@dataclass
class VerificationData:
    """Data class to hold verification information stored in cache."""
    code: str
    attempts: int = 0
    is_verified: bool = False


class PendingSubmissionVerification:
    """
    Cache-based verification code storage for pending submissions.
    
    This allows anonymous users to verify their identity via email
    before accessing pending submission details. Uses Django's cache
    instead of database for temporary storage.
    """
    
    CACHE_PREFIX = 'pending_submission_verify'
    
    def __init__(self, submission_id: str, email: str):
        self.submission_id = submission_id
        self.email = email.lower()
        self._cache_key = self._make_cache_key()
    
    def _make_cache_key(self) -> str:
        """Generate a unique cache key for this submission/email combination."""
        # Hash the email for privacy in cache keys
        email_hash = hashlib.sha256(self.email.encode()).hexdigest()[:16]
        return f'{self.CACHE_PREFIX}:{self.submission_id}:{email_hash}'
    
    def _get_data(self) -> Optional[VerificationData]:
        """Retrieve verification data from cache."""
        data = cache.get(self._cache_key)
        if data:
            return VerificationData(**data)
        return None
    
    def _set_data(self, data: VerificationData, timeout: int = None) -> None:
        """Store verification data in cache."""
        if timeout is None:
            timeout = CODE_EXPIRY_MINUTES * 60
        cache.set(self._cache_key, {
            'code': data.code,
            'attempts': data.attempts,
            'is_verified': data.is_verified,
        }, timeout=timeout)
    
    def create_code(self) -> str:
        """
        Create a new verification code.
        
        Any existing code will be replaced.
        """
        code = generate_verification_code()
        data = VerificationData(code=code)
        self._set_data(data)
        return code
    
    def verify(self, code: str) -> tuple[bool, str]:
        """
        Attempt to verify with the provided code.
        
        Returns a tuple of (success, message).
        """
        data = self._get_data()
        
        if data is None:
            return False, 'no_code_found'
        
        if data.is_verified:
            return True, 'already_verified'
        
        if data.attempts >= MAX_ATTEMPTS:
            return False, 'max_attempts'
        
        data.attempts += 1
        
        if data.code == code:
            data.is_verified = True
            # Keep verified status in cache for a bit longer
            self._set_data(data, timeout=CODE_EXPIRY_MINUTES * 60 * 2)
            return True, 'verified'
        
        self._set_data(data)
        remaining = MAX_ATTEMPTS - data.attempts
        return False, f'incorrect:{remaining}'
    
    @property
    def attempts_remaining(self) -> int:
        """Get the number of verification attempts remaining."""
        data = self._get_data()
        if data is None:
            return MAX_ATTEMPTS
        return max(0, MAX_ATTEMPTS - data.attempts)
    
    @classmethod
    def create_or_refresh(cls, submission_id: str, email: str) -> 'PendingSubmissionVerification':
        """
        Create a new verification instance and generate a fresh code.
        
        Any existing code for this submission/email will be replaced.
        """
        instance = cls(submission_id, email)
        instance.create_code()
        return instance
    
    @classmethod
    def get_code(cls, submission_id: str, email: str) -> Optional[str]:
        """
        Get the current verification code (for sending in email).
        
        Returns None if no code exists.
        """
        instance = cls(submission_id, email)
        data = instance._get_data()
        return data.code if data else None
