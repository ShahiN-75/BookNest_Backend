
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import math

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




# Search rooms by price and room type
@app.route("/api/v1.0/rooms/search", methods=["GET"])
def search_rooms():
    max_price_input = request.args.get("max_price")
    room_type = request.args.get("room_type")

    if max_price_input is None and not room_type:
        return jsonify({
            "message": "Provide max_price or room_type"
        }), 400

    # Check that the price is valid
    max_price = None

    if max_price_input is not None:
        try:
            max_price = float(max_price_input)
        except ValueError:
            return jsonify({
                "message": "Invalid maximum price"
            }), 400

        if max_price < 0 or not math.isfinite(max_price):
            return jsonify({
                "message": "Invalid maximum price"
            }), 400

    matching_rooms = []
    room_groups = db.rooms.find({}, {"_id": 0})

    for group in room_groups:
        group_type = group["room_type"]

        if room_type and group_type.lower() != room_type.strip().lower():
            continue

        rooms = group["room_category"]["rooms"]

        for room in rooms:
            if max_price is not None:

                # Find the standard nightly rate
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

            # Keep the room type with each matching room
            room_data = room.copy()
            room_data["room_type"] = group_type
            matching_rooms.append(room_data)

    return jsonify(matching_rooms), 200

# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)
