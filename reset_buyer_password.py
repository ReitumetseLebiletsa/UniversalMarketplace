from getpass import getpass
from app import app, db, User

username = "TestBuyer01"

with app.app_context():
    buyer = User.query.filter_by(username=username).first()

    if buyer is None:
        print("Buyer account not found.")
    else:
        new_password = getpass("Enter a new password (minimum 12 characters): ")
        confirm_password = getpass("Confirm the new password: ")

        if len(new_password) < 12:
            print("Password must be at least 12 characters.")
        elif new_password != confirm_password:
            print("Passwords do not match.")
        else:
            buyer.set_password(new_password)
            db.session.commit()
            print("Password reset successfully for", buyer.username)