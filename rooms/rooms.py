
import math

from flask import Blueprint, jsonify, request
import json
import extensions
from decorators import admin_required


# Blueprint for room management endpoints
rooms_bp = Blueprint("rooms", __name__)


# GET all rooms
@rooms_bp.route("/api/v1.0/rooms", methods=["GET"])
def show_all_rooms():

    all_rooms = []

    room_groups = extensions.db.rooms.find({}, {"_id": 0})

    for group in room_groups:

        room_type = group["room_type"]

        rooms = group["room_category"]["rooms"]

        for room in rooms:

            room_data = room.copy()

            room_data["room_type"] = room_type

            all_rooms.append(room_data)

    return jsonify(all_rooms), 200


# GET one room by ID
@rooms_bp.route("/api/v1.0/rooms/<int:room_id>", methods=["GET"])
def show_one_room(room_id):

    room_group = extensions.db.rooms.find_one(
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
@rooms_bp.route("/api/v1.0/rooms", methods=["POST"])
@admin_required
def add_room():

    data = request.get_json(silent=True)

    if data is None and not request.is_json:
        data = request.form.to_dict()

        if "room" in data:
            try:
                data["room"] = json.loads(data["room"])
            except (json.JSONDecodeError, TypeError):
                return jsonify({
                    "message": "Room details must contain valid JSON"
                }), 400

    if not isinstance(data, dict):
        return jsonify({
            "message": "A valid JSON object or form data is required"
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
    existing_room = extensions.db.rooms.find_one({
        "room_category.rooms.room_id": room_id
    })

    if existing_room is not None:
        return jsonify({
            "message": "Room ID already exists"
        }), 409

    # Add room inside the matching room group
    result = extensions.db.rooms.update_one(
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
@rooms_bp.route("/api/v1.0/rooms/<int:room_id>", methods=["PUT"])
@admin_required
def update_room(room_id):

    data = request.get_json(silent=True)

    if data is None and not request.is_json:
        data = request.form.to_dict()

        if "base_price" in data:
            try:
                data["base_price"] = float(data["base_price"])
            except (ValueError, TypeError):
                return jsonify({
                    "message": "A valid base_price is required"
                }), 400

    if not isinstance(data, dict):
        return jsonify({
            "message": "Valid JSON or form data is required"
        }), 400

    # Check whether the room exists
    room_group = extensions.db.rooms.find_one({
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
    result = extensions.db.rooms.update_one(
        {"room_category.rooms.room_id": room_id},
        {
            "$set": {
                "room_category.rooms.$.base_price_per_night": new_price
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
@rooms_bp.route("/api/v1.0/rooms/<int:room_id>", methods=["DELETE"])
@admin_required
def delete_room(room_id):

    result = extensions.db.rooms.update_one(
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
@rooms_bp.route("/api/v1.0/rooms/search", methods=["GET"])
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
    room_groups = extensions.db.rooms.find({}, {"_id": 0})

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
