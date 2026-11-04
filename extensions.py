
from pymongo import MongoClient

# Shared MongoDB connection
client = None
db = None


def init_db(app):
    global client, db

    client = MongoClient(
        app.config["MONGO_URI"],
        serverSelectionTimeoutMS=5000
    )

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

    return db
