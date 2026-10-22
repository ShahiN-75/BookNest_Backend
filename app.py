
from flask import Flask, jsonify
from pymongo import MongoClient
from pymongo.errors import PyMongoError

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


# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)
