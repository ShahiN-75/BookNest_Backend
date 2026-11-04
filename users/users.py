from flask import Blueprint, jsonify, request, g
import extensions
from decorators import token_required, admin_required

users_bp = Blueprint("users", __name__)


# Get the authenticated customer's profile
@users_bp.route("/api/v1.0/users/me", methods=["GET"])
@token_required
def get_my_profile():

    user = g.current_user

    return jsonify({
        "user_id": user["user_id"],
        "profile": {
            "personal_details": user["profile"]["personal_details"],
            "contact": user["profile"]["contact"]
        },
        "account": {
            "role": user["account"]["role"],
            "registered": user["account"]["authentication"]["registered"]
        }
    }), 200


