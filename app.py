
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import math
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from config import Config
import bcrypt
import re
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
import secrets
import jwt
from functools import wraps
from flask import g


# Create our Flask application
app = Flask(__name__)

# Load settings from config.py
app.config.from_object(Config)

# Create MongoDB client
client = MongoClient(
    app.config["MONGO_URI"],
    serverSelectionTimeoutMS=5000
)

# Select our BookNest database
db = client[app.config["MONGO_DB_NAME"]]


# Keep user IDs unique
db.User.create_index("user_id", unique=True)

# Keep registered email addresses unique
db.User.create_index(
    "profile.contact.email",
    unique=True,
    partialFilterExpression={
        "profile.contact.email": {"$type": "string"}
    }
)

# Verify JWT before allowing access to protected endpoints
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):

        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({
                "message": "Authentication token is required"
            }), 401

        token = auth_header[7:].strip()

        if not token:
            return jsonify({
                "message": "Authentication token is required"
            }), 401

        try:
            payload = jwt.decode(
                token,
                app.config["SECRET_KEY"],
                algorithms=["HS256"],
                options={"require": ["user_id", "role", "exp"]}
            )

            user_id = payload["user_id"]

            if not isinstance(user_id, int) or isinstance(user_id, bool):
                return jsonify({
                    "message": "Invalid authentication token"
                }), 401

            # Confirm the user still exists and is registered
            user = db.User.find_one({
                "user_id": user_id,
                "account.authentication.registered": True
            })

            if user is None:
                return jsonify({
                    "message": "User account not found"
                }), 401

            g.current_user = user

        except jwt.ExpiredSignatureError:
            return jsonify({
                "message": "Authentication token has expired"
            }), 401

        except jwt.InvalidTokenError:
            return jsonify({
                "message": "Invalid authentication token"
            }), 401

        except PyMongoError:
            return jsonify({
                "message": "Database error during authentication"
            }), 503

        return f(*args, **kwargs)

    return decorated

def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        user = g.current_user

        if user["account"]["role"] != "admin":
            return jsonify({
                "message": "Administrator access required"
            }), 403

        return f(*args, **kwargs)

    return decorated

def can_manage_booking(booking):
    current_user = g.current_user

    # Admin can access any booking
    if current_user["account"]["role"] == "admin":
        return True

    # Customer can access only their own booking
    return booking["user_id"] == current_user["user_id"]

# Welcome endpoint
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "message": "Welcome to BookNest API",
        "status": "running"
    }), 200


# Database health-check endpoint
@app.route("/api/v1.0/health", methods=["GET"])
def health_check():
    try:
        db.command("ping")

        return jsonify({
            "status": "ok",
            "database": "connected",
            "collections": db.list_collection_names()
        }), 200

    except PyMongoError:
        return jsonify({
            "status": "error",
            "database": "unavailable"
        }), 503


# Temporary endpoint to test reading MongoDB data
@app.route("/api/v1.0/database-test", methods=["GET"])
def database_test():
    try:
        room_document = db.rooms.find_one(
            {},
            {"_id": 0}
        )

        if room_document is None:
            return jsonify({
                "message": "No room documents found"
            }), 404

        return jsonify({
            "message": "Successfully retrieved MongoDB data",
            "data": room_document
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database query failed"
        }), 503


# GET all rooms
@app.route("/api/v1.0/rooms", methods=["GET"])
def show_all_rooms():

    all_rooms = []

    room_groups = db.rooms.find({}, {"_id": 0})

    for group in room_groups:

        room_type = group["room_type"]

        rooms = group["room_category"]["rooms"]

        for room in rooms:

            room_data = room.copy()

            room_data["room_type"] = room_type

            all_rooms.append(room_data)

    return jsonify(all_rooms), 200


# GET one room by ID
@app.route("/api/v1.0/rooms/<int:room_id>", methods=["GET"])
def show_one_room(room_id):

    room_group = db.rooms.find_one(
        {"room_category.rooms.room_id": room_id},
        {"_id": 0}
    )

    if room_group is None:
        return jsonify({
            "message": "Room not found"
        }), 404

    rooms = room_group["room_category"]["rooms"]

    for room in rooms:
        if room["room_id"] == room_id:

            room_data = room.copy()

            room_data["room_type"] = room_group["room_type"]

            return jsonify(room_data), 200

    return jsonify({
        "message": "Room not found"
    }), 404


# POST - Add a new room
@app.route("/api/v1.0/rooms", methods=["POST"])
@admin_required
def add_room():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "message": "A JSON object is required"
        }), 400

    room_type = data.get("room_type")
    new_room = data.get("room")

    if not isinstance(room_type, str) or not isinstance(new_room, dict):
        return jsonify({
            "message": "Room type and room details are required"
        }), 400

    room_id = new_room.get("room_id")

    if type(room_id) is not int:
        return jsonify({
            "message": "Room ID must be an integer"
        }), 400

    # Check if room ID already exists
    existing_room = db.rooms.find_one({
        "room_category.rooms.room_id": room_id
    })

    if existing_room is not None:
        return jsonify({
            "message": "Room ID already exists"
        }), 409

    # Add room inside the matching room group
    result = db.rooms.update_one(
        {"room_type": room_type},
        {
            "$push": {
                "room_category.rooms": new_room
            }
        }
    )

    if result.matched_count == 0:
        return jsonify({
            "message": "Room type not found"
        }), 404

    return jsonify({
        "message": "Room added successfully",
        "room_id": room_id
    }), 201


# PUT - Update an existing room
@app.route("/api/v1.0/rooms/<int:room_id>", methods=["PUT"])
@admin_required
def update_room(room_id):

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "message": "A JSON object is required"
        }), 400

    # Check whether the room exists
    room_group = db.rooms.find_one({
        "room_category.rooms.room_id": room_id
    })

    if room_group is None:
        return jsonify({
            "message": "Room not found"
        }), 404

    # Validate the new price
    new_price = data.get("base_price")

    if type(new_price) not in (int, float) or new_price < 0:
        return jsonify({
            "message": "A valid base_price is required"
        }), 400

    # Update only the matching room inside the nested array
    result = db.rooms.update_one(
        {"room_category.rooms.room_id": room_id},
        {
            "$set": {
                "room_category.rooms.$.pricing.base_price": new_price
            }
        }
    )

    if result.matched_count == 0:
        return jsonify({
            "message": "Room not found"
        }), 404

    return jsonify({
        "message": "Room updated successfully",
        "room_id": room_id,
        "new_price": new_price
    }), 200


# DELETE - Remove a room by ID
@app.route("/api/v1.0/rooms/<int:room_id>", methods=["DELETE"])
@admin_required
def delete_room(room_id):

    result = db.rooms.update_one(
        {"room_category.rooms.room_id": room_id},
        {
            "$pull": {
                "room_category.rooms": {
                    "room_id": room_id
                }
            }
        }
    )

    if result.modified_count == 0:
        return jsonify({
            "message": "Room not found"
        }), 404

    return jsonify({
        "message": "Room deleted successfully",
        "room_id": room_id
    }), 200






# Search rooms by price, type, capacity and availability
@app.route("/api/v1.0/rooms/search", methods=["GET"])
def search_rooms():
    max_price_input = request.args.get("max_price")
    room_type = request.args.get("room_type")
    guests_input = request.args.get("guests")
    status = request.args.get("status")

    if all(value is None for value in
           [max_price_input, room_type, guests_input, status]):
        return jsonify({
            "message": "Provide at least one search filter"
        }), 400

    # Validate the maximum price
    max_price = None

    if max_price_input is not None:
        try:
            max_price = float(max_price_input)
        except ValueError:
            return jsonify({"message": "Invalid maximum price"}), 400

        if max_price < 0 or not math.isfinite(max_price):
            return jsonify({"message": "Invalid maximum price"}), 400

    # Validate the number of guests
    guests = None

    if guests_input is not None:
        try:
            guests = int(guests_input)
        except ValueError:
            return jsonify({"message": "Invalid number of guests"}), 400

        if guests < 1:
            return jsonify({"message": "Invalid number of guests"}), 400

    # Check the availability status
    if status is not None:
        status = status.strip().lower()

        if status != "available":
            return jsonify({
                "message": "Invalid availability status"
            }), 400

    matching_rooms = []
    room_groups = db.rooms.find({}, {"_id": 0})

    for group in room_groups:
        group_type = group["room_type"]

        if room_type and group_type.lower() != room_type.strip().lower():
            continue

        rooms = group["room_category"]["rooms"]

        for room in rooms:

            if guests is not None:
                capacity = room.get("room_information", {}).get(
                    "occupancy", {}
                ).get("maximum_guests")

                if type(capacity) is not int or capacity < guests:
                    continue

            if max_price is not None:
                rate_plans = room.get("pricing", {}).get("rate_plans", [])

                standard_plan = next(
                    (
                        plan for plan in rate_plans
                        if plan.get("plan_name") == "Standard Rate"
                    ),
                    None
                )

                if standard_plan is None:
                    continue

                price = standard_plan.get(
                    "rate_details", {}
                ).get("base_price_per_night")

                if type(price) not in (int, float):
                    continue

                if price > max_price:
                    continue

            # Check the room's current availability
            if status is not None:
                room_status = room.get("room_management", {}).get(
                    "availability", {}
                ).get("current_status", "")

                if room_status.lower() != status:
                    continue

            room_data = room.copy()
            room_data["room_type"] = group_type
            matching_rooms.append(room_data)

    return jsonify(matching_rooms), 200


# Get all bookings
@app.route("/api/v1.0/bookings", methods=["GET"])
@admin_required
def get_all_bookings():
    bookings_list = []

    booking_groups = db.Bookings.find({}, {"_id": 0})

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
@app.route("/api/v1.0/bookings/<int:booking_id>", methods=["GET"])
@token_required
def get_booking_by_id(booking_id):

    booking_group = db.Bookings.find_one(
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
@app.route("/api/v1.0/bookings", methods=["POST"])
@token_required
def add_booking():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"message": "Valid JSON data is required"}), 400

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
    existing_booking = db.Bookings.find_one({
        "rooms.months.bookings.booking_id": booking_id
    })

    if existing_booking:
        return jsonify({"message": "Booking ID already exists"}), 409

    # Find the correct room inside the nested collection
    booking_group = db.Bookings.find_one({
        "rooms.room_id": room_id
    })

    if not booking_group:
        return jsonify({"message": "Room not found in bookings collection"}), 404

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
    result = db.Bookings.update_one(
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

        result = db.Bookings.update_one(
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
        return jsonify({"message": "Booking could not be saved"}), 500

    return jsonify({
        "message": "Booking created successfully",
        "booking": new_booking
    }), 201


# Update booking by ID
@app.route("/api/v1.0/bookings/<int:booking_id>", methods=["PUT"])
@token_required
def update_booking(booking_id):

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"message": "Valid JSON data is required"}), 400

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
    booking_group = db.Bookings.find_one({
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

                    result = db.Bookings.update_one(
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
@app.route("/api/v1.0/bookings/<int:booking_id>/cancel", methods=["PATCH"])
@token_required
def cancel_booking(booking_id):

    # Find the booking in MongoDB
    booking_group = db.Bookings.find_one({
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
                    result = db.Bookings.update_one(
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
@app.route("/api/v1.0/bookings/<int:booking_id>", methods=["DELETE"])
@token_required
def delete_booking(booking_id):

    # Find the booking in the collection
    booking_group = db.Bookings.find_one({
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
                    result = db.Bookings.update_one(
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
@app.route("/api/v1.0/bookings/statistics", methods=["GET"])
@admin_required
def get_booking_statistics():

    total_bookings = 0
    confirmed_bookings = 0
    cancelled_bookings = 0
    total_booking_value = 0

    # Retrieve all booking groups from MongoDB
    booking_groups = db.Bookings.find({}, {"_id": 0})

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
@app.route("/api/v1.0/bookings/occupancy", methods=["GET"])
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

    for group in db.rooms.find({}, {"_id": 0}):
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

    booking_groups = db.Bookings.find({}, {"_id": 0})

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
@app.route("/api/v1.0/bookings/average-stay", methods=["GET"])
@admin_required
def get_average_length_of_stay():

    total_bookings = 0
    total_nights = 0

    # Retrieve booking documents from MongoDB
    booking_groups = db.Bookings.find({}, {"_id": 0})

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
@app.route("/api/v1.0/bookings/monthly-revenue", methods=["GET"])
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
    booking_groups = db.Bookings.find({}, {"_id": 0})

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


# Generate monthly room utilisation report
@app.route("/api/v1.0/rooms/utilisation", methods=["GET"])
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

    for group in db.rooms.find({}, {"_id": 0}):

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
    booking_groups = db.Bookings.find({}, {"_id": 0})

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



# Register a new customer account
@app.route("/api/v1.0/register", methods=["POST"])
def register_user():

    data = request.get_json(silent=True)

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
    existing_user = db.User.find_one({
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
                db.User.insert_one(new_user)
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

# Log in a registered customer
@app.route("/api/v1.0/login", methods=["POST"])
def login_user():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "message": "A valid JSON object is required"
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({
            "message": "Email and password are required"
        }), 400

    email = email.strip().lower()

    if not email or not password:
        return jsonify({
            "message": "Email and password are required"
        }), 400

    try:
        # Find the registered customer
        user = db.User.find_one({
            "profile.contact.email": email,
            "account.authentication.registered": True
        })

        if not user:
            return jsonify({
                "message": "Invalid email or password"
            }), 401

        password_hash = user["account"]["authentication"]["password_hash"]

        # Verify the password
        if not bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8")
        ):
            return jsonify({
                "message": "Invalid email or password"
            }), 401

        # Generate a JWT valid for 30 minutes
        token = jwt.encode(
            {
                "user_id": user["user_id"],
                "role": user["account"]["role"],
                "exp": datetime.now(timezone.utc) + timedelta(minutes=30)
            },
            app.config["SECRET_KEY"],
            algorithm="HS256"
        )

        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "user_id": user["user_id"],
                "first_name": user["profile"]["personal_details"]["first_name"],
                "last_name": user["profile"]["personal_details"]["last_name"],
                "email": email,
                "role": user["account"]["role"]
            }
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error during login"
        }), 500

# Get the authenticated customer's profile
@app.route("/api/v1.0/users/me", methods=["GET"])
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

# Admin-only endpoint to verify administrator access
@app.route("/api/v1.0/admin/dashboard", methods=["GET"])
@admin_required
def admin_dashboard():
    return jsonify({
        "message": "Welcome to the BookNest admin dashboard",
        "role": "admin"
    }), 200

# Get published reviews for a specific room
@app.route("/api/v1.0/rooms/<int:room_id>/reviews", methods=["GET"])
def get_room_reviews(room_id):

    # Check whether the room exists
    room = db.rooms.find_one({
        "room_category.rooms.room_id": room_id
    })

    if room is None:
        return jsonify({"message": "Room not found"}), 404

    # Retrieve published reviews for this room
    reviews = list(db.Reviews.find(
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
@app.route("/api/v1.0/rooms/<int:room_id>/reviews", methods=["POST"])
@token_required
def add_room_review(room_id):

    current_user = g.current_user
    user_id = current_user["user_id"]

    if current_user["account"]["role"] != "customer":
        return jsonify({"message": "Only customers can submit reviews"}), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"message": "Valid JSON data is required"}), 400

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
        room_exists = db.rooms.find_one({
            "room_category.rooms.room_id": room_id
        })

        if room_exists is None:
            return jsonify({"message": "Room not found"}), 404

        # Find the booking inside the nested collection
        booking_group = db.Bookings.find_one({
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
        existing_review = db.Reviews.find_one({
            "booking_id": booking_id
        })

        if existing_review is not None:
            return jsonify({
                "message": "This booking has already been reviewed"
            }), 409

        # Generate a unique review ID
        for attempt in range(5):
            review_id = secrets.randbelow(900000000) + 100000000

            if db.Reviews.find_one({"review_id": review_id}) is None:
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

        db.Reviews.insert_one(new_review)

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
@app.route("/api/v1.0/user/reviews/<int:review_id>", methods=["PUT"])
@token_required
def update_review(review_id):

    current_user = g.current_user
    user_id = current_user["user_id"]

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"message": "Valid JSON data is required"}), 400

    try:
        # Find the review
        review = db.Reviews.find_one({"review_id": review_id})

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

        result = db.Reviews.update_one(
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
@app.route("/api/v1.0/user/reviews/<int:review_id>", methods=["DELETE"])
@token_required
def delete_review(review_id):

    user_id = g.current_user["user_id"]

    try:
        # Find the requested review
        review = db.Reviews.find_one({"review_id": review_id})

        if review is None:
            return jsonify({"message": "Review not found"}), 404

        # Customers can only delete their own reviews
        if review["user_id"] != user_id:
            return jsonify({
                "message": "You cannot delete another customer's review"
            }), 403

        # Delete the review belonging to this customer
        result = db.Reviews.delete_one({
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


# Admin: View all customer reviews
@app.route("/api/v1.0/admin/reviews", methods=["GET"])
@token_required
@admin_required
def admin_get_all_reviews():

    try:
        reviews = list(db.Reviews.find({}, {"_id": 0}))

        return jsonify({
            "total_reviews": len(reviews),
            "reviews": reviews
        }), 200

    except PyMongoError:
        return jsonify({
            "message": "Database error while fetching reviews"
        }), 503


# Admin: Delete any customer review
@app.route("/api/v1.0/admin/reviews/<int:review_id>", methods=["DELETE"])
@token_required
@admin_required
def admin_delete_review(review_id):

    try:
        # Check whether the review exists
        review = db.Reviews.find_one({"review_id": review_id})

        if review is None:
            return jsonify({
                "message": "Review not found"
            }), 404

        # Delete the selected review
        result = db.Reviews.delete_one({"review_id": review_id})

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


# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)
