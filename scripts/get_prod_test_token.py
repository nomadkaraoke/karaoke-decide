#!/usr/bin/env python3
"""Generate a production test token for E2E testing.

This script generates a JWT token for a specified user (default: andrew@beveridge.uk)
by connecting to production Firestore and using the production JWT_SECRET.

Prerequisites:
- gcloud CLI authenticated with access to nomadkaraoke project
- Access to Secret Manager secrets

Usage:
    # Generate token for default test user
    python scripts/get_prod_test_token.py

    # Generate token for a specific user
    python scripts/get_prod_test_token.py --email user@example.com

    # Export as environment variable
    export PROD_TEST_TOKEN=$(python scripts/get_prod_test_token.py)

    # Run tests with token
    PROD_TEST_TOKEN=$(python scripts/get_prod_test_token.py) npx playwright test e2e/production-comprehensive.spec.ts
"""

import argparse
import hashlib
import sys
from datetime import UTC, datetime, timedelta

try:
    from google.cloud import firestore, secretmanager
    from jose import jwt
except ImportError:
    print("Error: Required packages not installed.", file=sys.stderr)
    print("Run: pip install google-cloud-firestore google-cloud-secret-manager python-jose", file=sys.stderr)
    sys.exit(1)

# Configuration
PROJECT_ID = "nomadkaraoke"
DEFAULT_TEST_EMAIL = "andrew@beveridge.uk"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 168  # 7 days for test tokens


def get_jwt_secret() -> str:
    """Fetch JWT_SECRET from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    # Secret is named "karaoke-decide-jwt-secret" in the project
    name = f"projects/{PROJECT_ID}/secrets/karaoke-decide-jwt-secret/versions/latest"

    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Error fetching JWT_SECRET: {e}", file=sys.stderr)
        print("Make sure you have access to the nomadkaraoke project secrets.", file=sys.stderr)
        sys.exit(1)


def get_user_by_email(email: str) -> dict | None:
    """Look up a user in Firestore by email."""
    db = firestore.Client(project=PROJECT_ID)
    email_hash = hashlib.sha256(email.lower().encode()).hexdigest()

    doc = db.collection("users").document(email_hash).get()
    if doc.exists:
        return doc.to_dict()
    return None


def generate_jwt(user_id: str, email: str, jwt_secret: str) -> str:
    """Generate a JWT token for the user."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=JWT_EXPIRATION_HOURS)

    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    return jwt.encode(payload, jwt_secret, algorithm=JWT_ALGORITHM)


def main():
    parser = argparse.ArgumentParser(description="Generate a production test token for E2E testing")
    parser.add_argument(
        "--email",
        default=DEFAULT_TEST_EMAIL,
        help=f"Email of the test user (default: {DEFAULT_TEST_EMAIL})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print additional information to stderr",
    )
    args = parser.parse_args()

    if args.verbose:
        print(f"Looking up user: {args.email}", file=sys.stderr)

    # Get the user from Firestore
    user = get_user_by_email(args.email)
    if user is None:
        print(f"Error: User '{args.email}' not found in Firestore", file=sys.stderr)
        sys.exit(1)

    user_id = user.get("user_id")
    if not user_id:
        print("Error: User document missing user_id field", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Found user: {user_id}", file=sys.stderr)
        print("Fetching JWT_SECRET from Secret Manager...", file=sys.stderr)

    # Get the JWT secret
    jwt_secret = get_jwt_secret()

    if args.verbose:
        print("Generating token...", file=sys.stderr)

    # Generate the token
    token = generate_jwt(user_id, args.email, jwt_secret)

    # Print token to stdout (for easy piping to env var)
    print(token)

    if args.verbose:
        print(f"\nToken valid for {JWT_EXPIRATION_HOURS} hours", file=sys.stderr)
        print("\nUsage:", file=sys.stderr)
        print(f"  export PROD_TEST_TOKEN={token[:50]}...", file=sys.stderr)


if __name__ == "__main__":
    main()
