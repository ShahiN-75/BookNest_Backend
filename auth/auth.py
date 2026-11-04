
from flask import Blueprint, current_app, g, jsonify, request
from pymongo.errors import DuplicateKeyError, PyMongoError

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone

import re
import secrets
import extensions
from decorators import token_required

auth_bp = Blueprint("auth", __name__)


# Register a new customer account
@auth_bp.route("/api/v1.0/register", methods=["POST"])
def register_user():

    data = request.get_json(silent=True)

    if data is None and request.is_json is False:
        data = request.form.to_dict()

    if not isinstance(data, dict):
        return jsonify({
            "message": "A valid JSON object is required"
        }), 400

    first_name = data.get("first_name")
    last_name = data.get("last_name")
    email = data.get("email")
    password = data.get("password")

    if not all(isinstance(value, str) for value in [
        first_name, last_name, email, password
    ]):
        return jsonify({
            "message": "First name, last name, email and password are required"
        }), 400

    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()

    if not first_name or not last_name or not email or not password:
        return jsonify({
            "message": "All registration fields are required"
        }), 400

    if len(first_name) > 100 or len(last_name) > 100:
        return jsonify({
            "message": "Names must not exceed 100 characters"
        }), 400

    email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    if len(email) > 254 or not re.fullmatch(email_pattern, email):
        return jsonify({
            "message": "Please provide a valid email address"
        }), 400

    if len(password) < 8 or len(password.encode("utf-8")) > 72:
        return jsonify({
            "message": "Password must be between 8 and 72 bytes"
        }), 400

    # Check for an existing registered email
    existing_user = extensions.db.User.find_one({
        "profile.contact.email": email
    })

    if existing_user:
        return jsonify({
            "message": "An account with this email already exists"
        }), 409

    # Hash the password before saving
    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    try:
        # Retry if a randomly generated user ID already exists
        for attempt in range(5):

            new_user_id = secrets.randbelow(900000000) + 100000000

            new_user = {
                "user_id": new_user_id,
                "profile": {
                    "personal_details": {
                        "first_name": first_name,
                        "last_name": last_name
                    },
                    "contact": {
                        "email": email,
                        "phone": None
                    }
                },
                "account": {
                    "role": "customer",
                    "authentication": {
                        "registered": True,
                        "password_hash": password_hash
                    }
                }
            }

            try:
                extensions.db.User.insert_one(new_user)
                break

            except DuplicateKeyError as error:
                if error.details and error.details.get("keyPattern") == {"user_id": 1}:
                    continue

                return jsonify({
                    "message": "An account with this email already exists"
                }), 409

        else:
            return jsonify({
                "message": "Could not generate a unique user ID"
            }), 500

    except PyMongoError:
        return jsonify({
            "message": "Database error during registration"
        }), 500

    return jsonify({
        "message": "User registered successfully",
        "user": {
            "user_id": new_user_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "role": "customer"
        }
    }), 201



# Verify Basic credentials and issue a short-lived access token
@auth_bp.route("/api/v1.0/login", methods=["GET", "POST"])
def login_user():
    auth = request.authorization
    challenge = {"WWW-Authenticate": 'Basic realm="BookNest Login"'}

    if not auth or auth.type.lower() != "basic":
        return jsonify({"message": "Basic Authentication is required"}), 401, challenge

    email = (auth.username or "").strip().lower()
    password = auth.password

    if not email or not password:
        return jsonify({"message": "Email and password are required"}), 401, challenge

    try:
        user = extensions.db.User.find_one({
            "profile.contact.email": email,
            "account.authentication.registered": True
        })

        if not user:
            return jsonify({"message": "Invalid email or password"}), 401, challenge

        password_hash = user["account"]["authentication"].get("password_hash")

        if not password_hash or not bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        ):
            return jsonify({"message": "Invalid email or password"}), 401, challenge

        if not current_app.config.get("SECRET_KEY"):
            return jsonify({"message": "Authentication is not configured"}), 503

        now = datetime.now(timezone.utc)
        token = jwt.encode({
            "user_id": user["user_id"],
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + timedelta(minutes=30)
        }, current_app.config["SECRET_KEY"], algorithm="HS256")

        return jsonify({
            "message": "Login successful",
            "token": token,
            "token_type": "JWT",
            "expires_in_minutes": 30,
            "user": {
                "user_id": user["user_id"],
                "first_name": user["profile"]["personal_details"]["first_name"],
                "last_name": user["profile"]["personal_details"]["last_name"],
                "email": email,
                "role": user["account"]["role"]
            }
        }), 200

    except (KeyError, TypeError, ValueError):
        return jsonify({"message": "Invalid user authentication data"}), 401
    except PyMongoError:
        return jsonify({"message": "Database error during login"}), 503


# Revoke the current JWT without creating a fifth MongoDB collection
@auth_bp.route("/api/v1.0/logout", methods=["POST"])
@token_required
def logout_user():
    try:
        result = extensions.db.User.update_one(
            {"user_id": g.current_user["user_id"]},
            {"$addToSet": {
                "account.authentication.revoked_jtis": g.current_token_jti
            }}
        )
        if result.matched_count != 1:
            return jsonify({"message": "Account not found"}), 401
    except PyMongoError:
        return jsonify({"message": "Database error during logout"}), 503

    return jsonify({"message": "Logout successful"}), 200
