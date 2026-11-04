from functools import wraps

import jwt
from flask import current_app, g, jsonify, request
from pymongo.errors import PyMongoError

import extensions


# Verify the JWT supplied by an authenticated customer or administrator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("x-access-token")

        if not token:
            return jsonify({"message": "Access token is required"}), 401

        try:
            payload = jwt.decode(
                token,
                current_app.config["SECRET_KEY"],
                algorithms=["HS256"],
                options={"require": ["exp", "iat", "jti", "user_id"]}
            )
            user_id = payload["user_id"]
            token_id = payload["jti"]

            if type(user_id) is not int or not isinstance(token_id, str):
                return jsonify({"message": "Invalid access token"}), 401

            user = extensions.db.User.find_one({
                "user_id": user_id,
                "account.authentication.registered": True
            })

            if not user:
                return jsonify({"message": "Account is unavailable"}), 401

            authentication = user.get("account", {}).get("authentication", {})
            if token_id in authentication.get("revoked_jtis", []):
                return jsonify({"message": "Token has been revoked"}), 401

            g.current_user = user
            g.current_token_jti = token_id

        except jwt.ExpiredSignatureError:
            return jsonify({"message": "Access token has expired"}), 401
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return jsonify({"message": "Invalid access token"}), 401
        except PyMongoError:
            return jsonify({"message": "Database error during authentication"}), 503

        return f(*args, **kwargs)

    return decorated


# Restrict access to administrators
def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        user = g.current_user

        if user["account"]["role"] != "admin":
            return jsonify({"message": "Administrator access required"}), 403

        return f(*args, **kwargs)

    return decorated


# Check whether the current user can manage a booking
def can_manage_booking(booking):
    current_user = g.current_user

    if current_user["account"]["role"] == "admin":
        return True

    return booking["user_id"] == current_user["user_id"]
