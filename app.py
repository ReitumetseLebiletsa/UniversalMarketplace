import os
import math
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash


# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "dev-only-change-this-before-deployment",
)
app.config["SQLALCHEMY_DATABASE_URI"] = (
    "sqlite:///" + str(BASE_DIR / "marketplace.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."


# --------------------------------------------------
# USER MODEL — MUST BE DEFINED BEFORE USER LOADER
# --------------------------------------------------

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="buyer")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------
# SOURCING REQUEST MODEL
# --------------------------------------------------

class SourcingRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    buyer_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
        index=True,
    )

    buyer_user = db.relationship(
        "User",
        foreign_keys=[buyer_user_id],
    )

    product_name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    source_country = db.Column(db.String(50), nullable=False)
    delivery_country = db.Column(db.String(50), nullable=False)
    target_price = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), nullable=False)
    buyer_name = db.Column(db.String(120), nullable=False)
    buyer_contact = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(30), default="New", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------
# SUPPLIER MODEL
# --------------------------------------------------

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    country = db.Column(db.String(50), nullable=False)
    contact_name = db.Column(db.String(120), nullable=True)
    contact_details = db.Column(db.String(150), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------
# QUOTATION MODEL
# --------------------------------------------------

class Quotation(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    sourcing_request_id = db.Column(
        db.Integer,
        db.ForeignKey("sourcing_request.id"),
        nullable=False,
    )

    supplier_id = db.Column(
        db.Integer,
        db.ForeignKey("supplier.id"),
        nullable=False,
    )

    product_cost = db.Column(db.Float, nullable=False)
    sourcing_fee = db.Column(db.Float, default=0, nullable=False)
    delivery_cost = db.Column(db.Float, default=0, nullable=False)
    other_costs = db.Column(db.Float, default=0, nullable=False)
    selling_price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False)

    status = db.Column(
        db.String(30),
        default="Draft",
        nullable=False,
    )

    buyer_approved_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sourcing_request = db.relationship("SourcingRequest")
    supplier = db.relationship("Supplier")


# Create missing tables without deleting existing records.
with app.app_context():
    db.create_all()


# --------------------------------------------------
# CONSTANTS AND ACCESS CONTROL
# --------------------------------------------------

REQUEST_STATUSES = [
    "New",
    "Sourcing",
    "Quotation Ready",
    "Approved",
    "Purchased",
    "Delivered",
    "Cancelled",
]

ADMIN_ROLES = ("admin", "super_admin", "company_admin")


def admin_required(view_function):
    @wraps(view_function)
    @login_required
    def wrapped_view(*args, **kwargs):
        if current_user.role not in ADMIN_ROLES:
            abort(403)

        return view_function(*args, **kwargs)

    return wrapped_view


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


# --------------------------------------------------
# BUYER REGISTRATION
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please complete all required fields.", "error")

        elif len(password) < 12:
            flash("Password must be at least 12 characters.", "error")

        elif password != confirm_password:
            flash("Passwords do not match.", "error")

        elif User.query.filter(
            (User.username == username) | (User.email == email)
        ).first():
            flash("Username or email is already registered.", "error")

        else:
            buyer = User(
                username=username,
                email=email,
                role="buyer",
                active=True,
            )
            buyer.set_password(password)

            db.session.add(buyer)
            db.session.commit()

            flash(
                "Buyer account created. You can now log in.",
                "success",
            )
            return redirect(url_for("login"))

    return render_template("register.html")


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        if current_user.role in ADMIN_ROLES:
            return redirect(url_for("company_dashboard"))

        if current_user.role == "buyer":
            return redirect(url_for("home"))

        abort(403)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and user.is_active and user.check_password(password):
            login_user(user)

            next_page = request.args.get("next")

            # Permit only local paths, not external redirects.
            if (
                next_page
                and next_page.startswith("/")
                and not next_page.startswith("//")
            ):
                return redirect(next_page)

            if user.role in ADMIN_ROLES:
                return redirect(url_for("company_dashboard"))

            if user.role == "buyer":
                return redirect(url_for("home"))

            logout_user()
            abort(403)

        flash("Invalid username or password.", "error")

    return render_template("login.html")


# --------------------------------------------------
# LOGOUT — POST ONLY
# --------------------------------------------------

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------
# BUYER SOURCING REQUESTS
# --------------------------------------------------

@app.route("/request-product", methods=["GET", "POST"])
@login_required
def request_product():
    if current_user.role != "buyer":
        abort(403)

    if request.method == "POST":
        product_name = request.form.get("product_name", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        buyer_name = request.form.get("buyer_name", "").strip()
        buyer_contact = request.form.get("buyer_contact", "").strip()
        source_country = request.form.get("source_country", "").strip()
        delivery_country = request.form.get("delivery_country", "").strip()
        currency = request.form.get("currency", "").strip().upper()

        try:
            quantity = int(request.form.get("quantity", "0"))
            raw_price = request.form.get("target_price", "").strip()
            target_price = float(raw_price) if raw_price else None
        except (TypeError, ValueError):
            flash("Please enter a valid quantity and target price.", "error")
            return redirect(url_for("request_product"))

        allowed_countries = {"Lesotho", "South Africa"}
        allowed_currencies = {"LSL", "ZAR"}

        if not all([
            product_name,
            category,
            description,
            buyer_name,
            buyer_contact,
            source_country,
            delivery_country,
        ]):
            flash("Please complete all required fields.", "error")
            return redirect(url_for("request_product"))

        if source_country not in allowed_countries:
            flash("Please select a valid sourcing country.", "error")
            return redirect(url_for("request_product"))

        if delivery_country not in allowed_countries:
            flash("Please select a valid delivery country.", "error")
            return redirect(url_for("request_product"))

        if currency not in allowed_currencies:
            flash("Please select LSL or ZAR.", "error")
            return redirect(url_for("request_product"))

        if (
            quantity < 1
            or (target_price is not None and (
                not math.isfinite(target_price) or target_price < 0
            ))
        ):
            flash(
                "Quantity must be at least 1 and price must be valid.",
                "error",
            )
            return redirect(url_for("request_product"))

        new_request = SourcingRequest(
            buyer_user_id=current_user.id,
            product_name=product_name,
            category=category,
            description=description,
            quantity=quantity,
            source_country=source_country,
            delivery_country=delivery_country,
            target_price=target_price,
            currency=currency,
            buyer_name=buyer_name,
            buyer_contact=buyer_contact,
        )

        db.session.add(new_request)
        db.session.commit()

        flash(
            "Your sourcing request has been submitted successfully.",
            "success",
        )

        return render_template(
            "request_success.html",
            request_id=new_request.id,
        )

    return render_template("request_product.html")


# --------------------------------------------------
# BUYER'S OWN REQUESTS
# --------------------------------------------------

@app.route("/my-requests")
@login_required
def my_requests():
    if current_user.role != "buyer":
        abort(403)

    buyer_requests = SourcingRequest.query.filter_by(
        buyer_user_id=current_user.id
    ).order_by(
        SourcingRequest.created_at.desc()
    ).all()

    return render_template(
        "my_requests.html",
        requests=buyer_requests,
    )


# --------------------------------------------------
# COMPANY DASHBOARD
# --------------------------------------------------

@app.route("/company")
@admin_required
def company_dashboard():
    all_requests = SourcingRequest.query.order_by(
        SourcingRequest.created_at.desc()
    ).all()

    status_counts = {
        status: SourcingRequest.query.filter_by(status=status).count()
        for status in REQUEST_STATUSES
    }

    return render_template(
        "company_dashboard.html",
        requests=all_requests,
        status_counts=status_counts,
        statuses=REQUEST_STATUSES,
    )


# --------------------------------------------------
# BUYER ACCOUNT MANAGEMENT
# --------------------------------------------------

@app.route("/company/buyers")
@admin_required
def buyer_accounts():
    buyers = User.query.filter_by(role="buyer").order_by(
        User.created_at.desc()
    ).all()

    return render_template(
        "buyer_accounts.html",
        buyers=buyers,
    )


# --------------------------------------------------
# SUPPLIER MANAGEMENT
# --------------------------------------------------

@app.route("/company/suppliers/new", methods=["GET", "POST"])
@admin_required
def add_supplier():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        country = request.form.get("country", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        contact_details = request.form.get("contact_details", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name or not contact_details:
            flash(
                "Supplier name and contact details are required.",
                "error",
            )
            return redirect(url_for("add_supplier"))

        if country not in {"Lesotho", "South Africa"}:
            flash("Please select Lesotho or South Africa.", "error")
            return redirect(url_for("add_supplier"))

        supplier = Supplier(
            name=name,
            country=country,
            contact_name=contact_name or None,
            contact_details=contact_details,
            notes=notes or None,
        )

        db.session.add(supplier)
        db.session.commit()

        flash(f"Supplier '{name}' saved successfully.", "success")
        return redirect(url_for("list_suppliers"))

    return render_template("supplier_form.html")


@app.route("/company/suppliers")
@admin_required
def list_suppliers():
    suppliers = Supplier.query.order_by(
        Supplier.name.asc()
    ).all()

    return render_template(
        "suppliers.html",
        suppliers=suppliers,
    )


# --------------------------------------------------
# UPDATE REQUEST STATUS — ADMIN ONLY
# --------------------------------------------------

@app.route(
    "/company/request/<int:request_id>/status",
    methods=["POST"],
)
@admin_required
def update_request_status(request_id):
    sourcing_request = db.session.get(
        SourcingRequest,
        request_id,
    )

    if sourcing_request is None:
        abort(404)

    new_status = request.form.get("status", "").strip()

    if new_status not in REQUEST_STATUSES:
        abort(400, description="Invalid request status.")

    sourcing_request.status = new_status
    db.session.commit()

    flash(
        f"Request UM-{sourcing_request.id} updated to {new_status}.",
        "success",
    )

    return redirect(url_for("company_dashboard"))


# --------------------------------------------------
# CREATE QUOTATION — ADMIN ONLY
# --------------------------------------------------

@app.route(
    "/company/quotations/new",
    methods=["GET", "POST"],
)
@admin_required
def create_quotation():
    if request.method == "POST":
        try:
            sourcing_request_id = int(
                request.form.get("sourcing_request_id", "")
            )
            supplier_id = int(
                request.form.get("supplier_id", "")
            )

            product_cost = float(
                request.form.get("product_cost", "")
            )
            sourcing_fee = float(
                request.form.get("sourcing_fee", "0")
            )
            delivery_cost = float(
                request.form.get("delivery_cost", "0")
            )
            other_costs = float(
                request.form.get("other_costs", "0")
            )
            profit_amount = float(
                request.form.get("profit_amount", "0")
            )
            expires_days = int(
                request.form.get("expires_days", "3")
            )

        except (TypeError, ValueError):
            flash("Please enter valid quotation details.", "error")
            return redirect(url_for("create_quotation"))

        sourcing_request = db.session.get(
            SourcingRequest,
            sourcing_request_id,
        )
        supplier = db.session.get(Supplier, supplier_id)

        if sourcing_request is None or supplier is None:
            flash(
                "Please select an existing request and supplier.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        amounts = [
            product_cost,
            sourcing_fee,
            delivery_cost,
            other_costs,
            profit_amount,
        ]

        if any(
            amount < 0 or not math.isfinite(amount)
            for amount in amounts
        ):
            flash(
                "Costs and profit must be valid non-negative amounts.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if expires_days not in {1, 3, 7, 14}:
            flash(
                "Please select a valid quotation expiry.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        currency = request.form.get("currency", "").strip().upper()

        if currency not in {"ZAR", "LSL"}:
            flash(
                "Select ZAR or LSL as the quotation currency.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        selling_price = (
            product_cost
            + sourcing_fee
            + delivery_cost
            + other_costs
            + profit_amount
        )

        if not math.isfinite(selling_price):
            flash("The total quotation amount is too large.", "error")
            return redirect(url_for("create_quotation"))

        quotation = Quotation(
            sourcing_request_id=sourcing_request.id,
            supplier_id=supplier.id,
            product_cost=product_cost,
            sourcing_fee=sourcing_fee,
            delivery_cost=delivery_cost,
            other_costs=other_costs,
            selling_price=selling_price,
            currency=currency,
            status="Draft",
            expires_at=datetime.utcnow() + timedelta(days=expires_days),
        )

        db.session.add(quotation)
        db.session.commit()

        flash(
            f"Draft quotation QT-{quotation.id} saved. "
            "It has not been sent or approved by the buyer.",
            "success",
        )

        return redirect(url_for("list_quotations"))

    open_requests = SourcingRequest.query.filter(
        SourcingRequest.status.notin_(["Cancelled", "Delivered"])
    ).order_by(
        SourcingRequest.created_at.desc()
    ).all()

    suppliers = Supplier.query.order_by(
        Supplier.name.asc()
    ).all()

    return render_template(
        "quotation_form.html",
        requests=open_requests,
        suppliers=suppliers,
    )


# --------------------------------------------------
# QUOTATION LIST — ADMIN ALL / BUYER OWN ONLY
# --------------------------------------------------

@app.route("/company/quotations")
@login_required
def list_quotations():
    if current_user.role in ADMIN_ROLES:
        quotations = Quotation.query.order_by(
            Quotation.created_at.desc()
        ).all()

    elif current_user.role == "buyer":
        quotations = Quotation.query.join(
            SourcingRequest,
            Quotation.sourcing_request_id == SourcingRequest.id,
        ).filter(
            SourcingRequest.buyer_user_id == current_user.id
        ).order_by(
            Quotation.created_at.desc()
        ).all()

    else:
        abort(403)

    return render_template(
        "quotations.html",
        quotations=quotations,
    )


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.route("/health")
def health():
    return {
        "status": "healthy",
        "application": "Universal Marketplace",
    }


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)