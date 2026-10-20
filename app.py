
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


# Start Flask server
if __name__ == "__main__":
    app.run(debug=True)
