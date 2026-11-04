from flask import Blueprint, jsonify, request, g
from datetime import datetime, timezone
from pymongo.errors import PyMongoError
import secrets
import extensions
import json
from decorators import token_required, admin_required

reviews_bp = Blueprint("reviews", __name__)



# Get published reviews for a specific room
@reviews_bp.route("/api/v1.0/rooms/<int:room_id>/reviews", methods=["GET"])
def get_room_reviews(room_id):

    # Check whether the room exists
    room = extensions.db.rooms.find_one({
        "room_category.rooms.room_id": room_id
    })

    if room is None:
        return jsonify({"message": "Room not found"}), 404

    # Retrieve published reviews for this room
    reviews = list(extensions.db.Reviews.find(
        {
            "room_id": room_id,
            "review_management.status": "published"
        },
        {"_id": 0}
    ))

    return jsonify({
        "room_id": room_id,
        "total_reviews": len(reviews),
        "reviews": reviews
    }), 200



# Create a review for a completed room booking
@reviews_bp.route("/api/v1.0/rooms/<int:room_id>/reviews", methods=["POST"])
@token_required
def add_room_review(room_id):

    current_user = g.current_user
    user_id = current_user["user_id"]

    if current_user["account"]["role"] != "customer":
        return jsonify({"message": "Only customers can submit reviews"}), 403

    data = request.get_json(silent=True)

    # Accept both JSON and x-www-form-urlencoded data
    if data is None and not request.is_json:
        data = request.form.to_dict()

        try:
            if "booking_id" in data:
                data["booking_id"] = int(data["booking_id"])

            if "rating" in data:
                data["rating"] = json.loads(data["rating"])

            if "feedback" in data:
                data["feedback"] = json.loads(data["feedback"])

        except (ValueError, TypeError, json.JSONDecodeError):
            return jsonify({
                "message": "Invalid booking ID, rating or feedback"
            }), 400

    if not isinstance(data, dict):
        return jsonify({
            "message": "Valid JSON or form data is required"
        }), 400

    booking_id = data.get("booking_id")
    rating = data.get("rating")
    feedback = data.get("feedback")

    if type(booking_id) is not int or booking_id <= 0:
        return jsonify({"message": "Valid booking_id is required"}), 400

    if not isinstance(rating, dict) or not isinstance(feedback, dict):
        return jsonify({"message": "Rating and feedback are required"}), 400

    # Validate all rating categories
    rating_fields = ["overall", "cleanliness", "comfort", "service"]

    for field in rating_fields:
        value = rating.get(field)

        if type(value) is not int or not 1 <= value <= 5:
            return jsonify({
                "message": f"{field} rating must be between 1 and 5"
            }), 400

    title = feedback.get("title")
    comment = feedback.get("comment")

    if not isinstance(title, str) or not isinstance(comment, str):
        return jsonify({"message": "Review title and comment are required"}), 400

    title = title.strip()
    comment = comment.strip()

    if not title or not comment or len(title) > 150 or len(comment) > 2000:
        return jsonify({"message": "Invalid review title or comment"}), 400

    try:
        # Check that the room exists
        room_exists = extensions.db.rooms.find_one({
            "room_category.rooms.room_id": room_id
        })

        if room_exists is None:
            return jsonify({"message": "Room not found"}), 404

        # Find the booking inside the nested collection
        booking_group = extensions.db.Bookings.find_one({
            "rooms.months.bookings.booking_id": booking_id
        })

        if booking_group is None:
            return jsonify({"message": "Booking not found"}), 404

        matching_booking = None
        booked_room_id = None

        for room in booking_group.get("rooms", []):
            for month in room.get("months", []):
                for booking in month.get("bookings", []):
                    if booking.get("booking_id") == booking_id:
                        matching_booking = booking
                        booked_room_id = room["room_id"]
                        break

                if matching_booking is not None:
                    break

            if matching_booking is not None:
                break

        if matching_booking is None:
            return jsonify({"message": "Booking not found"}), 404

        # Check booking ownership and room
        if matching_booking["user_id"] != user_id:
            return jsonify({
                "message": "You cannot review another customer's booking"
            }), 403

        if booked_room_id != room_id:
            return jsonify({
                "message": "This booking does not belong to the selected room"
            }), 400

        # Only completed stays can be reviewed
        if matching_booking["reservation"]["status"] != "completed":
            return jsonify({
                "message": "You can only review a completed stay"
            }), 403

        check_out = datetime.strptime(
            matching_booking["reservation"]["stay"]["check_out"],
            "%Y-%m-%d"
        ).date()

        if check_out > datetime.now(timezone.utc).date():
            return jsonify({
                "message": "The stay has not finished yet"
            }), 403

        # Prevent duplicate reviews for the same booking
        existing_review = extensions.db.Reviews.find_one({
            "booking_id": booking_id
        })

        if existing_review is not None:
            return jsonify({
                "message": "This booking has already been reviewed"
            }), 409

        # Generate a unique review ID
        for attempt in range(5):
            review_id = secrets.randbelow(900000000) + 100000000

            if extensions.db.Reviews.find_one({"review_id": review_id}) is None:
                break
        else:
            return jsonify({
                "message": "Could not generate a review ID"
            }), 500

        now = datetime.now(timezone.utc).isoformat()

        new_review = {
            "review_id": review_id,
            "room_id": room_id,
            "user_id": user_id,
            "booking_id": booking_id,
            "review_details": {
                "rating": {
                    "overall": rating["overall"],
                    "categories": {
                        "cleanliness": rating["cleanliness"],
                        "comfort": rating["comfort"],
                        "service": rating["service"]
                    }
                },
                "feedback": {
                    "title": title,
                    "comment": comment
                }
            },
            "review_management": {
                "status": "published",
                "created_at": now,
                "updated_at": None
            }
        }

        extensions.db.Reviews.insert_one(new_review)

        # Remove MongoDB's internal ID from the response
        new_review.pop("_id", None)

        return jsonify({
            "message": "Review submitted successfully",
            "review": new_review
        }), 201

    except PyMongoError:
        return jsonify({
            "message": "Database error while creating review"
        }), 503




# Update an existing customer review
@reviews_bp.route("/api/v1.0/user/reviews/<int:review_id>", methods=["PUT"])
@token_required
def update_review(review_id):

    current_user = g.current_user
    user_id = current_user["user_id"]

    data = request.get_json(silent=True)

    # Accept both JSON and x-www-form-urlencoded data
    if data is None and not request.is_json:
        data = request.form.to_dict()

        try:
            if "rating" in data:
                data["rating"] = json.loads(data["rating"])

            if "feedback" in data:
                data["feedback"] = json.loads(data["feedback"])

        except (ValueError, TypeError, json.JSONDecodeError):
            return jsonify({
                "message": "Invalid rating or feedback data"
            }), 400

    if not isinstance(data, dict):
        return jsonify({
            "message": "Valid JSON or form data is required"
        }), 400

    try:
        # Find the review
        review = extensions.db.Reviews.find_one({"review_id": review_id})

        if review is None:
            return jsonify({"message": "Review not found"}), 404

        # Customers can only update their own reviews
        if review["user_id"] != user_id:
            return jsonify({
                "message": "You cannot update another customer's review"
            }), 403

        rating = data.get("rating")
        feedback = data.get("feedback")

        if not isinstance(rating, dict) or not isinstance(feedback, dict):
            return jsonify({
                "message": "Rating and feedback are required"
            }), 400

        rating_fields = ["overall", "cleanliness", "comfort", "service"]

        for field in rating_fields:
            value = rating.get(field)

            if type(value) is not int or not 1 <= value <= 5:
                return jsonify({
                    "message": f"{field} rating must be between 1 and 5"
                }), 400

        title = feedback.get("title")
        comment = feedback.get("comment")

        if not isinstance(title, str) or not isinstance(comment, str):
            return jsonify({
                "message": "Review title and comment are required"
            }), 400

        title = title.strip()
        comment = comment.strip()

        if not title or not comment or len(title) > 150 or len(comment) > 2000:
            return jsonify({
                "message": "Invalid review title or comment"
            }), 400

        # Update the nested review fields
        updated_at = datetime.now(timezone.utc).isoformat()

        result = extensions.db.Reviews.update_one(
            {"review_id": review_id, "user_id": user_id},
            {
                "$set": {
                    "review_details.rating.overall": rating["overall"],
                    "review_details.rating.categories.cleanliness": rating["cleanliness"],
                    "review_details.rating.categories.comfort": rating["comfort"],
                    "review_details.rating.categories.service": rating["service"],
                    "review_details.feedback.title": title,
                    "review_details.feedback.comment": comment,
                    "review_management.updated_at": updated_at
                }
            }
        )

        if result.matched_count == 0:
            return jsonify({"message": "Review not found"}), 404

        return jsonify({
            "message": "Review updated successfully",
            "review_id": review_id,
            "updated_at": updated_at
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error while updating review"
        }), 503



# Delete a customer's own review
@reviews_bp.route("/api/v1.0/user/reviews/<int:review_id>", methods=["DELETE"])
@token_required
def delete_review(review_id):

    user_id = g.current_user["user_id"]

    try:
        # Find the requested review
        review = extensions.db.Reviews.find_one({"review_id": review_id})

        if review is None:
            return jsonify({"message": "Review not found"}), 404

        # Customers can only delete their own reviews
        if review["user_id"] != user_id:
            return jsonify({
                "message": "You cannot delete another customer's review"
            }), 403

        # Delete the review belonging to this customer
        result = extensions.db.Reviews.delete_one({
            "review_id": review_id,
            "user_id": user_id
        })

        if result.deleted_count == 0:
            return jsonify({
                "message": "Review could not be deleted"
            }), 404

        return jsonify({
            "message": "Review deleted successfully",
            "review_id": review_id
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error while deleting review"
        }), 503
