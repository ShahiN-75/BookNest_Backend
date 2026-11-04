from datetime import datetime, timedelta
from datetime import date
from calendar import monthrange

from flask import Blueprint, jsonify, request, g

import extensions
from decorators import token_required, admin_required, can_manage_booking


bookings_bp = Blueprint("bookings", __name__)

# Get all bookings
@bookings_bp.route("/api/v1.0/bookings", methods=["GET"])
@admin_required
def get_all_bookings():
    bookings_list = []

    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    for group in booking_groups:
        for room in group["rooms"]:
            for month in room["months"]:
                for booking in month["bookings"]:
                    booking_data = booking.copy()
                    booking_data["room_id"] = room["room_id"]
                    booking_data["room_number"] = room["room_number"]
                    booking_data["room_type"] = group["room_type"]

                    bookings_list.append(booking_data)

    return jsonify(bookings_list), 200

# Get booking by ID
@bookings_bp.route("/api/v1.0/bookings/<int:booking_id>", methods=["GET"])
@token_required
def get_booking_by_id(booking_id):

    booking_group = extensions.db.Bookings.find_one(
        {"rooms.months.bookings.booking_id": booking_id},
        {"_id": 0}
    )

    if booking_group:
        for room in booking_group["rooms"]:
            for month in room["months"]:
                for booking in month["bookings"]:

                    if booking["booking_id"] == booking_id:
                        # Check booking ownership
                        if not can_manage_booking(booking):
                            return jsonify({
                                "message": "You are not allowed to access this booking"
                            }), 403

                        booking_data = booking.copy()
                        booking_data["room_id"] = room["room_id"]
                        booking_data["room_number"] = room["room_number"]
                        booking_data["room_type"] = booking_group["room_type"]

                        return jsonify(booking_data), 200

    return jsonify({"message": "Booking not found"}), 404



# Create a new booking
@bookings_bp.route("/api/v1.0/bookings", methods=["POST"])
@token_required
def add_booking():

    data = request.get_json(silent=True)

    if data is None and not request.is_json:
        data = request.form.to_dict()

    if not isinstance(data, dict):
        return jsonify({
            "message": "Valid JSON or form data is required"
        }), 400

    required_fields = [
        "booking_id", "user_id", "room_id",
        "check_in", "check_out", "price_per_night"
    ]

    for field in required_fields:
        if field not in data:
            return jsonify({"message": f"{field} is required"}), 400

    try:
        booking_id = int(data["booking_id"])
        user_id = int(data["user_id"])
        room_id = int(data["room_id"])

        check_in = datetime.strptime(data["check_in"], "%Y-%m-%d")
        check_out = datetime.strptime(data["check_out"], "%Y-%m-%d")

        price_per_night = float(data["price_per_night"])

    except (ValueError, TypeError, OverflowError):
        return jsonify({"message": "Invalid booking data"}), 400

    if booking_id <= 0 or user_id <= 0 or room_id <= 0:
        return jsonify({"message": "IDs must be positive integers"}), 400

    # Customers can only create bookings for themselves
    current_user = g.current_user

    if current_user["account"]["role"] != "admin":
        if user_id != current_user["user_id"]:
            return jsonify({
                "message": "You cannot create a booking for another user"
            }), 403

    if check_out <= check_in:
        return jsonify({"message": "Check-out must be after check-in"}), 400

    if price_per_night <= 0:
        return jsonify({"message": "Price must be greater than zero"}), 400

    # Check whether booking ID already exists
    existing_booking = extensions.db.Bookings.find_one({
        "rooms.months.bookings.booking_id": booking_id
    })

    if existing_booking:
        return jsonify({"message": "Booking ID already exists"}), 409

    # Find the correct room inside the nested collection
    booking_group = extensions.db.Bookings.find_one({
        "rooms.room_id": room_id
    })

    if not booking_group:
        return jsonify({
            "message": "Room not found in bookings collection"
        }), 404

    # Check existing reservations for this room
    for room in booking_group["rooms"]:
        if room["room_id"] == room_id:

            for month in room["months"]:
                for booking in month["bookings"]:

                    if booking["reservation"]["status"] in [
                        "cancelled", "no-show"
                    ]:
                        continue

                    stay = booking["reservation"]["stay"]

                    existing_check_in = datetime.strptime(
                        stay["check_in"], "%Y-%m-%d"
                    )

                    existing_check_out = datetime.strptime(
                        stay["check_out"], "%Y-%m-%d"
                    )

                    if check_in < existing_check_out and check_out > existing_check_in:
                        return jsonify({
                            "message": "Room is already booked for these dates"
                        }), 409

            break

    # Calculate booking price
    total_nights = (check_out - check_in).days
    total_price = round(total_nights * price_per_night, 2)

    # Generate nightly rates
    nightly_rates = []

    for day in range(total_nights):
        current_date = check_in + timedelta(days=day)

        nightly_rates.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "weekday": current_date.strftime("%A"),
            "rate": price_per_night
        })

    # Create the booking using our existing dataset structure
    new_booking = {
        "booking_id": booking_id,
        "user_id": user_id,

        "reservation": {
            "status": "confirmed",
            "stay": {
                "check_in": data["check_in"],
                "check_out": data["check_out"],
                "total_nights": total_nights
            }
        },

        "guest_information": {
            "party": [
                {"type": "adult", "count": 2},
                {"type": "child", "count": 0},
                {"type": "baby", "count": 0}
            ]
        },

        "booking_preferences": {
            "meal": {"code": "BB"},
            "additional_services": {
                "parking": {"required": False},
                "special_requests": {"count": 0}
            }
        },

        "payment_summary": {
            "pricing": {
                "price_per_night": price_per_night,
                "total_nights": total_nights,
                "total_price": total_price
            },
            "nightly_rates": nightly_rates
        }
    }

    # Month is based on the check-in date
    booking_month = check_in.strftime("%Y-%m")

    # Add booking to an existing month
    result = extensions.db.Bookings.update_one(
        {"rooms.room_id": room_id},
        {
            "$push": {
                "rooms.$[room].months.$[month].bookings": new_booking
            }
        },
        array_filters=[
            {"room.room_id": room_id},
            {"month.month": booking_month}
        ]
    )

    # If the month does not exist, create it
    if result.modified_count == 0:

        result = extensions.db.Bookings.update_one(
            {"rooms.room_id": room_id},
            {
                "$push": {
                    "rooms.$.months": {
                        "month": booking_month,
                        "bookings": [new_booking]
                    }
                }
            }
        )

    if result.modified_count == 0:
        return jsonify({
            "message": "Booking could not be saved"
        }), 500

    return jsonify({
        "message": "Booking created successfully",
        "booking": new_booking
    }), 201


# Update booking by ID
@bookings_bp.route("/api/v1.0/bookings/<int:booking_id>", methods=["PUT"])
@token_required
def update_booking(booking_id):

    data = request.get_json(silent=True)

    if data is None and not request.is_json:
        data = request.form.to_dict()

    if not isinstance(data, dict):
        return jsonify({
            "message": "Valid JSON or form data is required"
        }), 400

    required_fields = ["check_in", "check_out", "price_per_night"]

    for field in required_fields:
        if field not in data:
            return jsonify({"message": f"{field} is required"}), 400

    try:
        check_in = datetime.strptime(data["check_in"], "%Y-%m-%d")
        check_out = datetime.strptime(data["check_out"], "%Y-%m-%d")
        price_per_night = float(data["price_per_night"])
    except (ValueError, TypeError, OverflowError):
        return jsonify({"message": "Invalid booking data"}), 400

    if check_out <= check_in:
        return jsonify({"message": "Check-out must be after check-in"}), 400

    if price_per_night <= 0:
        return jsonify({"message": "Price must be greater than zero"}), 400

    # Find the booking inside MongoDB
    booking_group = extensions.db.Bookings.find_one({
        "rooms.months.bookings.booking_id": booking_id
    })

    if not booking_group:
        return jsonify({"message": "Booking not found"}), 404

    for room in booking_group["rooms"]:
        for month in room["months"]:
            for booking in month["bookings"]:

                if booking["booking_id"] == booking_id:
                    # Check booking ownership
                    if not can_manage_booking(booking):
                        return jsonify({
                            "message": "You are not allowed to update this booking"
                        }), 403

                    # Check for overlapping active bookings
                    for other_month in room["months"]:
                        for other_booking in other_month["bookings"]:

                            if other_booking["booking_id"] == booking_id:
                                continue

                            if other_booking["reservation"]["status"] in [
                                "cancelled", "no-show"
                            ]:
                                continue

                            stay = other_booking["reservation"]["stay"]

                            existing_in = datetime.strptime(
                                stay["check_in"], "%Y-%m-%d"
                            )
                            existing_out = datetime.strptime(
                                stay["check_out"], "%Y-%m-%d"
                            )

                            if check_in < existing_out and check_out > existing_in:
                                return jsonify({
                                    "message": "Room is already booked for these dates"
                                }), 409

                    total_nights = (check_out - check_in).days
                    total_price = round(
                        total_nights * price_per_night, 2
                    )

                    nightly_rates = []

                    for day in range(total_nights):
                        current_date = check_in + timedelta(days=day)

                        nightly_rates.append({
                            "date": current_date.strftime("%Y-%m-%d"),
                            "weekday": current_date.strftime("%A"),
                            "rate": price_per_night
                        })

                    updated_booking = booking.copy()

                    updated_booking["reservation"]["stay"] = {
                        "check_in": data["check_in"],
                        "check_out": data["check_out"],
                        "total_nights": total_nights
                    }

                    updated_booking["payment_summary"]["pricing"] = {
                        "price_per_night": price_per_night,
                        "total_nights": total_nights,
                        "total_price": total_price
                    }

                    updated_booking["payment_summary"]["nightly_rates"] = nightly_rates

                    # Find booking position in the nested array
                    booking_index = month["bookings"].index(booking)

                    result = extensions.db.Bookings.update_one(
                        {"_id": booking_group["_id"]},
                        {
                            "$set": {
                                "rooms.$[r].months.$[m].bookings.$[b]": updated_booking
                            }
                        },
                        array_filters=[
                            {"r.room_id": room["room_id"]},
                            {"m.month": month["month"]},
                            {"b.booking_id": booking_id}
                        ]
                    )

                    if result.modified_count == 0:
                        return jsonify({
                            "message": "No booking changes were saved"
                        }), 200

                    return jsonify({
                        "message": "Booking updated successfully",
                        "booking": updated_booking
                    }), 200

    return jsonify({"message": "Booking not found"}), 404

# Cancel booking by ID
@bookings_bp.route("/api/v1.0/bookings/<int:booking_id>/cancel", methods=["PATCH"])
@token_required
def cancel_booking(booking_id):

    # Find the booking in MongoDB
    booking_group = extensions.db.Bookings.find_one({
        "rooms.months.bookings.booking_id": booking_id
    })

    if not booking_group:
        return jsonify({"message": "Booking not found"}), 404

    # Find the specific booking inside the nested arrays
    for room in booking_group["rooms"]:
        for month in room["months"]:
            for booking in month["bookings"]:

                if booking["booking_id"] == booking_id:
                    # Only the booking owner or admin can cancel it
                    if not can_manage_booking(booking):
                        return jsonify({
                            "message": "You are not allowed to cancel this booking"
                        }), 403

                    # Check if already cancelled
                    if booking["reservation"]["status"] == "cancelled":
                        return jsonify({
                            "message": "Booking is already cancelled"
                        }), 409

                    # Update only the booking status
                    result = extensions.db.Bookings.update_one(
                        {"_id": booking_group["_id"]},
                        {
                            "$set": {
                                "rooms.$[r].months.$[m].bookings.$[b].reservation.status": "cancelled"
                            }
                        },
                        array_filters=[
                            {"r.room_id": room["room_id"]},
                            {"m.month": month["month"]},
                            {"b.booking_id": booking_id}
                        ]
                    )

                    if result.modified_count == 0:
                        return jsonify({
                            "message": "Booking cancellation failed"
                        }), 500

                    return jsonify({
                        "message": "Booking cancelled successfully",
                        "booking_id": booking_id,
                        "status": "cancelled"
                    }), 200

    return jsonify({"message": "Booking not found"}), 404



# Delete booking by ID
@bookings_bp.route("/api/v1.0/bookings/<int:booking_id>", methods=["DELETE"])
@token_required
def delete_booking(booking_id):

    # Find the booking in the collection
    booking_group = extensions.db.Bookings.find_one({
        "rooms.months.bookings.booking_id": booking_id
    })

    if not booking_group:
        return jsonify({"message": "Booking not found"}), 404

    # Locate the correct room and month
    for room in booking_group["rooms"]:
        for month in room["months"]:
            for booking in month["bookings"]:

                if booking["booking_id"] == booking_id:

                    # Only the booking owner or admin can delete it
                    if not can_manage_booking(booking):
                        return jsonify({
                            "message": "You are not allowed to delete this booking"
                        }), 403

                    # Remove only the matching booking
                    result = extensions.db.Bookings.update_one(
                        {"_id": booking_group["_id"]},
                        {
                            "$pull": {
                                "rooms.$[r].months.$[m].bookings": {
                                    "booking_id": booking_id
                                }
                            }
                        },
                        array_filters=[
                            {"r.room_id": room["room_id"]},
                            {"m.month": month["month"]}
                        ]
                    )

                    if result.modified_count == 0:
                        return jsonify({
                            "message": "Booking could not be deleted"
                        }), 409

                    return jsonify({
                        "message": "Booking deleted successfully",
                        "booking_id": booking_id
                    }), 200

    return jsonify({"message": "Booking not found"}), 404



# Calculate booking statistics
@bookings_bp.route("/api/v1.0/bookings/statistics", methods=["GET"])
@admin_required
def get_booking_statistics():

    total_bookings = 0
    confirmed_bookings = 0
    cancelled_bookings = 0
    total_booking_value = 0

    # Retrieve all booking groups from MongoDB
    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    # Navigate the nested booking structure
    for group in booking_groups:
        for room in group.get("rooms", []):
            for month in room.get("months", []):
                for booking in month.get("bookings", []):

                    total_bookings += 1

                    status = booking["reservation"]["status"]

                    if status == "confirmed":
                        confirmed_bookings += 1

                    if status == "cancelled":
                        cancelled_bookings += 1

                    # Calculate value of active bookings
                    if status not in ["cancelled", "no-show"]:
                        total_price = booking["payment_summary"]["pricing"]["total_price"]
                        total_booking_value += float(total_price)

    # Calculate cancellation percentage
    if total_bookings > 0:
        cancellation_rate = round(
            (cancelled_bookings / total_bookings) * 100, 2
        )
    else:
        cancellation_rate = 0

    return jsonify({
        "total_bookings": total_bookings,
        "confirmed_bookings": confirmed_bookings,
        "cancelled_bookings": cancelled_bookings,
        "cancellation_rate": cancellation_rate,
        "total_booking_value": round(total_booking_value, 2)
    }), 200


# Calculate monthly room occupancy
@bookings_bp.route("/api/v1.0/bookings/occupancy", methods=["GET"])
@admin_required
def get_room_occupancy():

    month = request.args.get("month")

    if not month:
        return jsonify({
            "message": "Month is required. Use YYYY-MM format"
        }), 400

    try:
        selected_month = datetime.strptime(month, "%Y-%m")

        year = selected_month.year
        month_number = selected_month.month

        days_in_month = monthrange(year, month_number)[1]

        month_start = date(year, month_number, 1)
        month_end = month_start + timedelta(days=days_in_month)

    except (ValueError, OverflowError):
        return jsonify({
            "message": "Invalid month format. Use YYYY-MM"
        }), 400

    # Count rooms from the rooms collection
    room_ids = set()

    for group in extensions.db.rooms.find({}, {"_id": 0}):
        room_category = group.get("room_category", {})

        for room in room_category.get("rooms", []):
            if "room_id" in room:
                room_ids.add(room["room_id"])

    total_rooms = len(room_ids)

    if total_rooms == 0:
        return jsonify({
            "message": "No rooms found"
        }), 404

    available_room_nights = total_rooms * days_in_month

    # Store unique occupied dates for each room
    occupied_nights = set()

    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    for group in booking_groups:
        for room in group.get("rooms", []):

            room_id = room.get("room_id")

            if room_id not in room_ids:
                continue

            for booking_month in room.get("months", []):
                for booking in booking_month.get("bookings", []):

                    status = booking["reservation"]["status"]

                    if status in ["cancelled", "no-show"]:
                        continue

                    stay = booking["reservation"]["stay"]

                    check_in = datetime.strptime(
                        stay["check_in"], "%Y-%m-%d"
                    ).date()

                    check_out = datetime.strptime(
                        stay["check_out"], "%Y-%m-%d"
                    ).date()

                    # Count only nights inside the selected month
                    start = max(check_in, month_start)
                    end = min(check_out, month_end)

                    current_date = start

                    while current_date < end:
                        occupied_nights.add((room_id, current_date))
                        current_date += timedelta(days=1)

    total_occupied_nights = len(occupied_nights)

    occupancy_rate = round(
        (total_occupied_nights / available_room_nights) * 100,
        2
    )

    return jsonify({
        "month": month,
        "total_rooms": total_rooms,
        "days_in_month": days_in_month,
        "available_room_nights": available_room_nights,
        "occupied_room_nights": total_occupied_nights,
        "occupancy_rate": occupancy_rate
    }), 200


# Calculate average length of stay
@bookings_bp.route("/api/v1.0/bookings/average-stay", methods=["GET"])
@admin_required
def get_average_length_of_stay():

    total_bookings = 0
    total_nights = 0

    # Retrieve booking documents from MongoDB
    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    # Navigate the nested booking structure
    for group in booking_groups:
        for room in group.get("rooms", []):
            for month in room.get("months", []):
                for booking in month.get("bookings", []):

                    status = booking["reservation"]["status"]

                    # Exclude cancelled and no-show bookings
                    if status in ["cancelled", "no-show"]:
                        continue

                    nights = booking["reservation"]["stay"]["total_nights"]

                    total_bookings += 1
                    total_nights += nights

    # Calculate average length of stay
    if total_bookings > 0:
        average_stay = round(total_nights / total_bookings, 2)
    else:
        average_stay = 0

    return jsonify({
        "total_active_bookings": total_bookings,
        "total_booked_nights": total_nights,
        "average_length_of_stay": average_stay
    }), 200


# Calculate monthly booking revenue
@bookings_bp.route("/api/v1.0/bookings/monthly-revenue", methods=["GET"])
@admin_required
def get_monthly_booking_revenue():

    month = request.args.get("month")

    if not month:
        return jsonify({
            "message": "Month is required. Use YYYY-MM format"
        }), 400

    # Validate the selected month
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

    total_revenue = 0
    total_bookings = 0
    total_booked_nights = 0

    # Retrieve booking documents
    booking_groups = extensions.db.Bookings.find({}, {"_id": 0})

    for group in booking_groups:
        for room in group.get("rooms", []):
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

                    # Find the nights inside the selected month
                    start = max(check_in, month_start)
                    end = min(check_out, month_end)

                    if start >= end:
                        continue

                    total_bookings += 1

                    nightly_rates = booking.get(
                        "payment_summary", {}
                    ).get("nightly_rates", [])

                    rate_by_date = {
                        rate["date"]: float(rate["rate"])
                        for rate in nightly_rates
                    }

                    default_rate = float(
                        booking["payment_summary"]["pricing"]["price_per_night"]
                    )

                    current_date = start

                    while current_date < end:

                        date_string = current_date.strftime("%Y-%m-%d")

                        nightly_price = rate_by_date.get(
                            date_string, default_rate
                        )

                        total_revenue += nightly_price
                        total_booked_nights += 1

                        current_date += timedelta(days=1)

    return jsonify({
        "month": month,
        "total_bookings": total_bookings,
        "total_booked_nights": total_booked_nights,
        "total_booking_revenue": round(total_revenue, 2),
        "currency": "GBP"
    }), 200

