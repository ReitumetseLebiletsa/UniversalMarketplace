from getpass import getpass

from app import app, db, User


def create_admin():
    with app.app_context():
        username = input("Choose administrator username: ").strip()
        email = input("Administrator email: ").strip().lower()

        if not username or not email:
            print("Username and email are required.")
            return

        existing_user = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing_user:
            print("That username or email already exists.")
            print("No account was changed.")
            return

        password = getpass("Choose a strong administrator password: ")
        confirm_password = getpass("Confirm password: ")

        if len(password) < 12:
            print("Use a password with at least 12 characters.")
            return

        if password != confirm_password:
            print("Passwords do not match.")
            return

        admin = User(
            username=username,
            email=email,
            role="super_admin",
            active=True
        )
        admin.set_password(password)

        db.session.add(admin)
        db.session.commit()

        print("Administrator account created successfully.")
        print("You can now sign in at http://127.0.0.1:5000/login")


if __name__ == "__main__":
    create_admin()