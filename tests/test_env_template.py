from __future__ import annotations

import re
import unittest
from pathlib import Path


_TEMPLATE = Path(__file__).resolve().parents[1] / ".env.example"
_SERVER_SECRET_PLACEHOLDER = "sb_secret_replace_with_server_only_key"
_EXPECTED_SUPABASE_SETTINGS = {
    "SUPABASE_URL": "https://your-project-ref.supabase.co",
    "SUPABASE_SECRET_KEY": _SERVER_SECRET_PLACEHOLDER,
    "SUPABASE_PROJECT_REF": "your-project-ref",
    "NEXT_PUBLIC_SUPABASE_URL": "",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "",
}


class EnvironmentTemplateTests(unittest.TestCase):
    """Only inspect the committed template, never runtime environment files.

    Assertions deliberately compare booleans rather than secret-bearing strings,
    so an accidental credential cannot be echoed in a test failure.
    """

    def test_supabase_settings_are_exact_safe_placeholders(self) -> None:
        settings: dict[str, str] = {}
        for line in _TEMPLATE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.removeprefix("export ").split("=", 1)
            name = name.strip()
            if "SUPABASE" not in name.upper():
                continue
            self.assertTrue(
                name in _EXPECTED_SUPABASE_SETTINGS,
                "Unexpected Supabase setting in .env.example; inspect privately.",
            )
            self.assertTrue(
                name not in settings,
                "Duplicate Supabase setting in .env.example; inspect privately.",
            )
            settings[name] = value.strip()
        self.assertTrue(
            settings == _EXPECTED_SUPABASE_SETTINGS,
            ".env.example Supabase settings must match safe placeholders only; inspect privately.",
        )

    def test_no_nonplaceholder_secret_tokens_anywhere_in_template(self) -> None:
        # Include comments and non-Supabase fields in the scan. Never report a
        # matched token or the source line, even if the check fails.
        tokens = re.findall(
            r"sb_secret_[A-Za-z0-9._-]+", _TEMPLATE.read_text(encoding="utf-8")
        )
        self.assertTrue(
            all(token == _SERVER_SECRET_PLACEHOLDER for token in tokens),
            "Non-placeholder secret-shaped value found in .env.example; inspect privately.",
        )


if __name__ == "__main__":
    unittest.main()
