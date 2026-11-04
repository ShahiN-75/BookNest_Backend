from flask import Blueprint, jsonify, request
from datetime import date, datetime, timedelta
from calendar import monthrange
import extensions
from pymongo.errors import PyMongoError
from decorators import admin_required

admin_bp = Blueprint("admin", __name__)


# Generate monthly room utilisation report
@admin_bp.route("/api/v1.0/rooms/utilisation", methods=["GET"])
@admin_required
def get_room_utilisation():

    month = request.args.get("month")

    if not month:
        return jsonify({
            "message": "Month is required. Use YYYY-MM format"
        }), 400

    try:
        selected_month = datetime.strptime(month, "%Y-%m")

        if selected_month.strftime("%Y-%m") != month:
            raise ValueError

        year = selected_month.year
        month_number = selected_month.month

        days_in_month = monthrange(year, month_number)[1]

        month_start = date(year, month_number, 1)
        month_end = month_start + timedelta(days=days_in_month)

    except (ValueError, OverflowError):
        return jsonify({
            "message": "Invalid month format. Use YYYY-MM"
        }), 400

    # Retrieve all rooms from the nested rooms collection
    room_data = {}

    for group in extensions.db.rooms.find({}, {"_id": 0}):

        room_type = group.get("room_type")
        room_category = group.get("room_category", {})

        for room in room_category.get("rooms", []):

            room_id = room["room_id"]

            room_data[room_id] = {
                "room_id": room_id,
                "room_number": room["room_number"],
                "room_type": room_type,
                "occupied_dates": set(),
                "booking_ids": set()
            }

    if not room_data:
        return jsonify({
            "message": "No rooms found"
        }), 404

    # Retrieve booking information
    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    for group in booking_groups:
        for room in group.get("rooms", []):

            room_id = room.get("room_id")

            if room_id not in room_data:
                continue

            for booking_month in room.get("months", []):
                for booking in booking_month.get("bookings", []):

                    status = booking["reservation"]["status"]

                    # Exclude cancelled and no-show bookings
                    if status in ["cancelled", "no-show"]:
                        continue

                    stay = booking["reservation"]["stay"]

                    check_in = datetime.strptime(
                        stay["check_in"], "%Y-%m-%d"
                    ).date()

                    check_out = datetime.strptime(
                        stay["check_out"], "%Y-%m-%d"
                    ).date()

                    # Find dates within the selected month
                    start = max(check_in, month_start)
                    end = min(check_out, month_end)

                    if start >= end:
                        continue

                    room_data[room_id]["booking_ids"].add(
                        booking["booking_id"]
                    )

                    current_date = start

                    while current_date < end:

                        room_data[room_id]["occupied_dates"].add(
                            current_date
                        )

                        current_date += timedelta(days=1)

    # Calculate utilisation for each room
    utilisation_report = []

    for room_id, details in room_data.items():

        booked_nights = len(details["occupied_dates"])

        utilisation_rate = round(
            (booked_nights / days_in_month) * 100,
            2
        )

        utilisation_report.append({
            "room_id": room_id,
            "room_number": details["room_number"],
            "room_type": details["room_type"],
            "total_bookings": len(details["booking_ids"]),
            "booked_nights": booked_nights,
            "available_nights": days_in_month,
            "utilisation_rate": utilisation_rate
        })

    # Show most utilised rooms first
    utilisation_report.sort(
        key=lambda room: room["utilisation_rate"],
        reverse=True
    )

    return jsonify({
        "month": month,
        "total_rooms": len(utilisation_report),
        "days_in_month": days_in_month,
        "rooms": utilisation_report
    }), 200

# Admin-only endpoint to verify administrator access
@admin_bp.route("/api/v1.0/admin/dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    return jsonify({
        "message": "Welcome to the BookNest admin dashboard",
        "role": "admin"
    }), 200


# Admin: View all customer reviews
@admin_bp.route("/api/v1.0/admin/reviews", methods=["GET"])
@admin_required
def admin_get_all_reviews():

    try:
        reviews = list(extensions.db.Reviews.find({}, {"_id": 0}))

        return jsonify({
            "total_reviews": len(reviews),
            "reviews": reviews
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error while fetching reviews"
        }), 503


# Admin: Delete any customer review
@admin_bp.route("/api/v1.0/admin/reviews/<int:review_id>", methods=["DELETE"])
@admin_required
def admin_delete_review(review_id):

    try:
        # Check whether the review exists
        review = extensions.db.Reviews.find_one({"review_id": review_id})

        if review is None:
            return jsonify({
                "message": "Review not found"
            }), 404

        # Delete the selected review
        result = extensions.db.Reviews.delete_one({"review_id": review_id})

        if result.deleted_count == 0:
            return jsonify({
                "message": "Review could not be deleted"
            }), 404

        return jsonify({
            "message": "Review deleted successfully by admin",
            "review_id": review_id
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error while deleting review"
        }), 503
