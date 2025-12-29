import os
import sys


def _add_repo_root_to_sys_path():
    here = os.path.dirname(__file__)
    root = os.path.abspath(os.path.join(here, os.pardir))
    if root not in sys.path:
        sys.path.insert(0, root)


_add_repo_root_to_sys_path()


def _ensure_minimum_env_for_imports():
    # Prevent import-time failures in treasury_digest.py due to required SMTP envs.
    os.environ.setdefault("SMTP_USER", "test@example.com")
    os.environ.setdefault("SMTP_PASS", "dummy-app-password")
    os.environ.setdefault("FROM_EMAIL", os.environ.get("SMTP_USER", "test@example.com"))
    os.environ.setdefault("TO_EMAILS", "test@example.com")
    # Defaults for optional settings
    os.environ.setdefault("SMTP_HOST", os.environ.get("SMTP_HOST", "smtp.gmail.com"))
    os.environ.setdefault("SMTP_SECURITY", os.environ.get("SMTP_SECURITY", "starttls"))
    # Avoid accidental email sending in tests
    os.environ.setdefault("DRY_RUN", "1")


_ensure_minimum_env_for_imports()
