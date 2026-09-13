from __future__ import annotations

import json
import unittest

from glofguard.security import redact_sensitive_text


class RedactionTests(unittest.TestCase):
    def test_json_quoted_credentials_are_redacted_without_losing_structure(self) -> None:
        original = {
            "authorization": "Bearer synthetic credential",
            "password": 'synthetic \\"quoted\\" password',
            "refresh_token": "synthetic-refresh-value",
            "status": "REMOTE_FAILED",
        }
        sanitized = json.loads(redact_sensitive_text(json.dumps(original)))
        self.assertEqual(sanitized["authorization"], "[REDACTED]")
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["refresh_token"], "[REDACTED]")
        self.assertEqual(sanitized["status"], "REMOTE_FAILED")

    def test_single_quoted_dict_and_unquoted_headers(self) -> None:
        sanitized = redact_sensitive_text(
            "{'api_key': 'synthetic private value', 'access_token': 'test-value'} "
            "Authorization: Bearer synthetic-header-token; password=test-password"
        )
        for value in ("synthetic private value", "test-value", "synthetic-header-token", "test-password"):
            self.assertNotIn(value, sanitized)
        self.assertEqual(sanitized.count("[REDACTED]"), 4)

    def test_bare_jwt_is_redacted(self) -> None:
        # Deliberately synthetic, not signed and never usable for authentication.
        synthetic_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljLXNpZ25hdHVyZQ"
        sanitized = redact_sensitive_text(f"provider rejected ({synthetic_jwt}); version=1.2.3")
        self.assertNotIn(synthetic_jwt, sanitized)
        self.assertIn("([REDACTED])", sanitized)
        self.assertIn("version=1.2.3", sanitized)

    def test_environment_style_names_and_url_passwords(self) -> None:
        sanitized = redact_sensitive_text(
            'SUPABASE_SECRET_KEY="synthetic secret" '
            "SUPABASE_SERVICE_ROLE_KEY='legacy-test-key' "
            "postgresql://test-user:test-pass@example.invalid/database"
        )
        for value in ("synthetic secret", "legacy-test-key", "test-user", "test-pass"):
            self.assertNotIn(value, sanitized)
        self.assertIn("postgresql://[REDACTED]@example.invalid/database", sanitized)

    def test_known_secrets_and_opaque_keys(self) -> None:
        sanitized = redact_sensitive_text(
            "error: arbitrary-test-credential sb_secret_SYNTHETIC sb_publishable_SYNTHETIC",
            secrets=("arbitrary-test-credential", ""),
        )
        self.assertEqual(sanitized, "error: [REDACTED] [REDACTED] [REDACTED]")

    def test_normal_error_diagnostics_remain_useful(self) -> None:
        message = "HTTP 401; lake_id=PKGL-00995; status=REMOTE_FAILED; retryable=False"
        self.assertEqual(redact_sensitive_text(message), message)

    def test_repeated_redaction_is_idempotent(self) -> None:
        sanitized = redact_sensitive_text(
            "apikey=sb_secret_SYNTHETIC; password=test-password; Authorization: Bearer sb_secret_SYNTHETIC"
        )
        self.assertEqual(sanitized, "apikey=[REDACTED]; password=[REDACTED]; Authorization: [REDACTED]")
        self.assertEqual(redact_sensitive_text(sanitized), sanitized)


if __name__ == "__main__":
    unittest.main()
