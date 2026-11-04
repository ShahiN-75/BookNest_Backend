
from flask import Flask, jsonify
from pymongo.errors import PyMongoError

from config import Config
from extensions import init_db

from rooms.rooms import rooms_bp
from bookings.bookings import bookings_bp
from admin.admin import admin_bp
from users.users import users_bp
from auth.auth import auth_bp
from reviews.reviews import reviews_bp


# Create the Flask application
app = Flask(__name__)

# Load application configuration
app.config.from_object(Config)

# Register application Blueprints
app.register_blueprint(rooms_bp)
app.register_blueprint(bookings_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(users_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(reviews_bp)

# Initialise the shared MongoDB connection
db = init_db(app)


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


# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)