
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import math
from datetime import datetime, timedelta

from config import Config


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
                        booking_data = booking.copy()
                        booking_data["room_id"] = room["room_id"]
                        booking_data["room_number"] = room["room_number"]
                        booking_data["room_type"] = booking_group["room_type"]

                        return jsonify(booking_data), 200

    return jsonify({"message": "Booking not found"}), 404


# Create a new booking
@app.route("/api/v1.0/bookings", methods=["POST"])
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



# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)
