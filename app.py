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
from werkzeug.utils import secure_filename
import uuid


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

# Product image upload settings
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB maximum

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

    buyer_decision = db.Column(
    db.String(20),
    nullable=True,
)

    buyer_approved_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sourcing_request = db.relationship("SourcingRequest")
    supplier = db.relationship("Supplier")

# --------------------------------------------------
# PUBLIC PRODUCT CATALOGUE MODEL
# --------------------------------------------------

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(180), nullable=False, index=True)
    category = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)

    price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="ZAR")

    country = db.Column(db.String(50), nullable=False)
    image_url = db.Column(db.String(500), nullable=True)

    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    published = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # --------------------------------------------------
# CUSTOMER PRODUCT ENQUIRY MODEL
# --------------------------------------------------

class ProductEnquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("product.id"),
        nullable=False,
        index=True,
    )

    customer_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
        index=True,
    )

    customer_name = db.Column(db.String(120), nullable=False)
    customer_email = db.Column(db.String(254), nullable=False)
    customer_contact = db.Column(db.String(120), nullable=True)

    quantity = db.Column(db.Integer, nullable=False, default=1)
    message = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(30), nullable=False, default="New")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship("Product")
    customer_user = db.relationship("User")

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
# CATEGORY BROWSING
# --------------------------------------------------

MARKETPLACE_CATEGORIES = [
    "Beauty & Personal Care",
    "Automotive & Mechanical",
    "Heavy Machinery",
    "General Wholesale",
    "Medical Equipment & Supplies",
    "Electronics: Cameras & Phones",
    "Adult Sexuality & Wellness",
    "Other",
]


@app.route("/categories/<path:category_name>")
def browse_category(category_name):
    category_slugs = {
        "beauty-personal-care": "Beauty & Personal Care",
        "automotive-mechanical": "Automotive & Mechanical",
        "heavy-machinery": "Heavy Machinery",
        "general-wholesale": "General Wholesale",
        "medical-equipment-supplies": "Medical Equipment & Supplies",
        "electronics-cameras-phones": "Electronics: Cameras & Phones",
        "adult-sexuality-wellness": "Adult Sexuality & Wellness",
        "other": "Other",
    }

    selected_category = category_slugs.get(
        category_name.casefold()
    )

    if selected_category is None:
        abort(404)

    # Do not expose private buyer sourcing requests as public listings.
    # Public product listings will be added separately.
    products = Product.query.filter_by(
    category=selected_category,
    published=True,
).order_by(
    Product.created_at.desc()
).all()

    return render_template(
    "category_products.html",
    category=selected_category,
    products=products,
)


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

@app.route(
    "/company/quotations/<int:quotation_id>/decision",
    methods=["POST"],
)
@login_required
def buyer_quotation_decision(quotation_id):
    # Only registered buyers can make quotation decisions.
    if current_user.role != "buyer":
        abort(403)

    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        abort(404)

    sourcing_request = db.session.get(
        SourcingRequest,
        quotation.sourcing_request_id,
    )

    # Verify that this quotation belongs to the logged-in buyer.
    if (
        sourcing_request is None
        or sourcing_request.buyer_user_id != current_user.id
    ):
        abort(403)

    # Buyers can decide only on quotations that have been sent.
    if quotation.status != "Sent":
        flash(
            "Only sent quotations can be approved or rejected.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    # Do not allow a decision after the quotation expires.
    if (
        quotation.expires_at is not None
        and quotation.expires_at <= datetime.utcnow()
    ):
        flash("This quotation has expired.", "error")
        return redirect(url_for("list_quotations"))

    # Read the buyer's requested decision.
    decision = request.form.get("decision", "").strip().lower()

    if decision not in ("approve", "reject"):
        abort(400)

    if quotation.buyer_decision is not None:
        flash(
            "You have already made a decision on this quotation.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    if decision == "approve":
        quotation.buyer_decision = "Approved"
        quotation.buyer_approved_at = datetime.utcnow()
        flash("Quotation approved successfully.", "success")
    else:
        quotation.buyer_decision = "Rejected"
        flash("Quotation rejected.", "success")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not save buyer quotation decision")
        flash(
            "Your decision could not be saved. Please try again.",
            "error",
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
    ADMIN_ROLES=ADMIN_ROLES,
    now=datetime.utcnow(),
)

@app.route("/company/quotations/compare/<int:request_id>")
@login_required
def compare_suppliers(request_id):
    sourcing_request = db.session.get(SourcingRequest, request_id)

    if sourcing_request is None:
        abort(404)

    # Administrators may compare all quotations.
    # Buyers may only compare quotations for their own requests.
    if current_user.role in ADMIN_ROLES:
        pass
    elif current_user.role == "buyer":
        if sourcing_request.buyer_user_id != current_user.id:
            abort(403)
    else:
        abort(403)

    quotations = Quotation.query.filter_by(
        sourcing_request_id=sourcing_request.id
    ).order_by(
        Quotation.created_at.desc()
    ).all()

    # Group by currency so ZAR and LSL are never compared directly.
    comparisons = {}

    for quotation in quotations:
        currency = quotation.currency
        total_cost = (
            quotation.product_cost
            + quotation.sourcing_fee
            + quotation.delivery_cost
            + quotation.other_costs
        )
        estimated_profit = quotation.selling_price - total_cost

        comparisons.setdefault(currency, []).append({
            "quotation": quotation,
            "total_cost": total_cost,
            "estimated_profit": estimated_profit,
        })

    for currency in comparisons:
        comparisons[currency].sort(
            key=lambda item: item["total_cost"]
        )

    return render_template(
        "supplier_comparison.html",
        sourcing_request=sourcing_request,
        comparisons=comparisons,
    )

# --------------------------------------------------
# SEND QUOTATION TO BUYER — ADMIN ONLY
# --------------------------------------------------

@app.route(
    "/company/quotations/<int:quotation_id>/send",
    methods=["POST"],
)
@admin_required
def send_quotation_to_buyer(quotation_id):
    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        abort(404)

    # Only draft quotations can be sent.
    if quotation.status != "Draft":
        flash(
            "Only draft quotations can be sent to the buyer.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    # The sourcing request must belong to a registered buyer.
    sourcing_request = db.session.get(
        SourcingRequest,
        quotation.sourcing_request_id,
    )

    if sourcing_request is None or sourcing_request.buyer_user_id is None:
        flash(
            "This request is not linked to a registered buyer. "
            "The quotation has not been sent.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    # Expired quotations must be recreated rather than sent.
    if (
        quotation.expires_at is not None
        and quotation.expires_at <= datetime.utcnow()
    ):
        flash(
            "This quotation has expired. Create a new quotation.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    quotation.status = "Sent"

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not send quotation")
        flash(
            "The quotation could not be sent. Please try again.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    flash(
        f"Quotation QT-{quotation.id} is now available "
        "for the buyer to review.",
        "success",
    )

    return redirect(url_for("list_quotations"))

# --------------------------------------------------
# PRODUCT MANAGEMENT — ADMIN ONLY
# --------------------------------------------------

@app.route("/company/products")
@admin_required
def manage_products():
    products = Product.query.order_by(
        Product.created_at.desc()
    ).all()

    return render_template(
        "company_products.html",
        products=products,
    )


@app.route("/company/products/new", methods=["GET", "POST"])
@admin_required
def create_product():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        country = request.form.get("country", "").strip()
        currency = request.form.get("currency", "").strip().upper()
        image_url = request.form.get("image_url", "").strip()
        image_file = request.files.get("image_file")

        try:
            price = float(request.form.get("price", ""))
            stock_quantity = int(
                request.form.get("stock_quantity", "0")
            )
        except (TypeError, ValueError):
            flash("Enter a valid price and stock quantity.", "error")
            return redirect(url_for("create_product"))

        if not name or not description:
            flash("Product name and description are required.", "error")
            return redirect(url_for("create_product"))

        if category not in MARKETPLACE_CATEGORIES:
            flash("Select a valid product category.", "error")
            return redirect(url_for("create_product"))

        if country not in {"Lesotho", "South Africa"}:
            flash("Select Lesotho or South Africa.", "error")
            return redirect(url_for("create_product"))

        if currency not in {"ZAR", "LSL"}:
            flash("Select ZAR or LSL.", "error")
            return redirect(url_for("create_product"))

        if not math.isfinite(price) or price < 0 or stock_quantity < 0:
            flash("Price and stock cannot be negative or invalid.", "error")
            return redirect(url_for("create_product"))

        if len(name) > 180 or len(description) > 5000:
            flash("The product name or description is too long.", "error")
            return redirect(url_for("create_product"))

        if image_url and not (
            image_url.startswith("https://")
            or image_url.startswith("http://")
        ):
            flash("Image URL must start with http:// or https://.", "error")
            return redirect(url_for("create_product"))

        # An uploaded image takes priority over an external image URL.
        if image_file and image_file.filename:
            original_name = secure_filename(image_file.filename)
            extension = Path(original_name).suffix.lower()
            allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

            if not original_name or extension not in allowed_extensions:
                flash(
                    "Choose a JPG, JPEG, PNG, WEBP or GIF image.",
                    "error",
                )
                return redirect(url_for("create_product"))

            # Read the actual file contents to check the image format.
            image_file.stream.seek(0)
            file_header = image_file.stream.read(12)
            image_file.stream.seek(0)

            valid_image = (
                file_header.startswith(b"\xff\xd8\xff")
                if extension in {".jpg", ".jpeg"}
                else file_header.startswith(b"\x89PNG\r\n\x1a\n")
                if extension == ".png"
                else file_header.startswith(b"RIFF")
                and file_header[8:12] == b"WEBP"
                if extension == ".webp"
                else file_header.startswith((b"GIF87a", b"GIF89a"))
                if extension == ".gif"
                else False
            )

            if not valid_image:
                flash(
                    "The uploaded file does not match its image extension.",
                    "error",
                )
                return redirect(url_for("create_product"))

            filename = f"{uuid.uuid4().hex}{extension}"
            image_path = Path(app.config["UPLOAD_FOLDER"]) / filename
            image_file.save(image_path)

            # Store a path that works with the existing image src attributes.
            image_url = url_for(
                "static",
                filename=f"uploads/{filename}",
            )

        product = Product(
            name=name,
            category=category,
            description=description,
            country=country,
            currency=currency,
            price=price,
            stock_quantity=stock_quantity,
            image_url=image_url or None,
            published=False,
        )

        db.session.add(product)
        db.session.commit()

        flash(
            f"Product '{product.name}' saved as a draft. "
            "Publish it when it is ready for customers.",
            "success",
        )
        return redirect(url_for("manage_products"))

    return render_template(
        "product_form.html",
        categories=MARKETPLACE_CATEGORIES,
    )

@app.route(
    "/company/products/<int:product_id>/edit",
    methods=["GET", "POST"],
)
@app.route(
    "/company/products/<int:product_id>/edit",
    methods=["GET", "POST"],
)
@admin_required
def edit_product(product_id):
    product = db.session.get(Product, product_id)

    if product is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        country = request.form.get("country", "").strip()
        currency = request.form.get("currency", "").strip().upper()
        image_url = request.form.get("image_url", "").strip()
        image_file = request.files.get("image_file")

        try:
            price = float(request.form.get("price", ""))
            stock_quantity = int(
                request.form.get("stock_quantity", "")
            )
        except (TypeError, ValueError):
            flash("Enter a valid price and stock quantity.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if not name or not description:
            flash("Product name and description are required.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if category not in MARKETPLACE_CATEGORIES:
            flash("Select a valid product category.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if country not in {"Lesotho", "South Africa"}:
            flash("Select Lesotho or South Africa.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if currency not in {"ZAR", "LSL"}:
            flash("Select ZAR or LSL.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if (
            not math.isfinite(price)
            or price < 0
            or stock_quantity < 0
        ):
            flash("Price and stock cannot be negative or invalid.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if len(name) > 180 or len(description) > 5000:
            flash("The product name or description is too long.", "error")
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        if image_url and not (
            image_url.startswith("https://")
            or image_url.startswith("http://")
        ):
            flash(
                "Image URL must start with http:// or https://.",
                "error",
            )
            return redirect(
                url_for("edit_product", product_id=product.id)
            )

        # Keep the current image unless a new URL or uploaded file is supplied.
        new_image_url = image_url or product.image_url

        if image_file and image_file.filename:
            original_name = secure_filename(image_file.filename)
            extension = Path(original_name).suffix.lower()
            allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

            if not original_name or extension not in allowed_extensions:
                flash(
                    "Choose a JPG, JPEG, PNG, WEBP or GIF image.",
                    "error",
                )
                return redirect(
                    url_for("edit_product", product_id=product.id)
                )

            image_file.stream.seek(0)
            file_header = image_file.stream.read(12)
            image_file.stream.seek(0)

            valid_image = (
                file_header.startswith(b"\xff\xd8\xff")
                if extension in {".jpg", ".jpeg"}
                else file_header.startswith(b"\x89PNG\r\n\x1a\n")
                if extension == ".png"
                else file_header.startswith(b"RIFF")
                and file_header[8:12] == b"WEBP"
                if extension == ".webp"
                else file_header.startswith((b"GIF87a", b"GIF89a"))
                if extension == ".gif"
                else False
            )

            if not valid_image:
                flash(
                    "The uploaded file does not match its image extension.",
                    "error",
                )
                return redirect(
                    url_for("edit_product", product_id=product.id)
                )

            filename = f"{uuid.uuid4().hex}{extension}"
            image_path = Path(app.config["UPLOAD_FOLDER"]) / filename
            image_file.save(image_path)

            new_image_url = url_for(
                "static",
                filename=f"uploads/{filename}",
            )

        product.name = name
        product.category = category
        product.description = description
        product.country = country
        product.currency = currency
        product.price = price
        product.stock_quantity = stock_quantity
        product.image_url = new_image_url or None

        db.session.commit()

        flash(f"Product '{product.name}' updated successfully.", "success")
        return redirect(url_for("manage_products"))

    return render_template(
        "product_form.html",
        product=product,
        categories=MARKETPLACE_CATEGORIES,
    )


@app.route(
    "/company/products/<int:product_id>/publish",
    methods=["POST"],
)
@admin_required
def toggle_product_published(product_id):
    product = db.session.get(Product, product_id)

    if product is None:
        abort(404)

    product.published = not product.published
    db.session.commit()

    state = "published" if product.published else "unpublished"
    flash(f"Product '{product.name}' is now {state}.", "success")

    return redirect(url_for("manage_products"))

# --------------------------------------------------
# PUBLIC PRODUCT DETAIL PAGE
# --------------------------------------------------

@app.route("/products/<int:product_id>")
def product_detail(product_id):
    product = Product.query.filter_by(
        id=product_id,
        published=True
    ).first_or_404()

    return render_template("product_detail.html", product=product)


# --------------------------------------------------
# CUSTOMER PRODUCT ENQUIRY SUBMISSION
# --------------------------------------------------

@app.route(
    "/products/<int:product_id>/enquire",
    methods=["POST"]
)
def submit_product_enquiry(product_id):
    product = Product.query.filter_by(
        id=product_id,
        published=True
    ).first_or_404()

    customer_name = request.form.get(
        "customer_name", ""
    ).strip()

    customer_email = request.form.get(
        "customer_email", ""
    ).strip().lower()

    customer_contact = request.form.get(
        "customer_contact", ""
    ).strip()

    message = request.form.get(
        "message", ""
    ).strip()

    try:
        quantity = int(request.form.get("quantity", "1"))
    except (TypeError, ValueError):
        quantity = 0

    if not customer_name or len(customer_name) > 120:
        flash("Please enter a valid name.", "error")
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    if (
        not customer_email
        or len(customer_email) > 254
        or "@" not in customer_email
        or "." not in customer_email.rsplit("@", 1)[-1]
    ):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    if len(customer_contact) > 120:
        flash("Your contact details are too long.", "error")
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    if quantity < 1 or quantity > 1000000:
        flash("Please enter a valid quantity.", "error")
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    if not message or len(message) > 5000:
        flash(
            "Please enter a message of up to 5,000 characters.",
            "error"
        )
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    enquiry = ProductEnquiry(
        product_id=product.id,
        customer_user_id=(
            current_user.id
            if current_user.is_authenticated
            and current_user.role == "buyer"
            else None
        ),
        customer_name=customer_name,
        customer_email=customer_email,
        customer_contact=customer_contact or None,
        quantity=quantity,
        message=message,
    )

    try:
        db.session.add(enquiry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not save product enquiry")
        flash(
            "We could not submit your enquiry. Please try again.",
            "error"
        )
        return redirect(url_for(
            "product_detail", product_id=product.id
        ))

    flash(
        "Your enquiry has been submitted successfully!",
        "success"
    )
    return redirect(url_for(
        "product_detail", product_id=product.id
    ))

# --------------------------------------------------
# COMPANY PRODUCT ENQUIRY DASHBOARD
# --------------------------------------------------

@app.route("/company/product-enquiries")
@admin_required
def company_product_enquiries():
    enquiries = (
        ProductEnquiry.query
        .join(Product)
        .order_by(ProductEnquiry.created_at.desc())
        .all()
    )

    return render_template(
        "company_product_enquiries.html",
        enquiries=enquiries,
    )

# --------------------------------------------------
# UPDATE PRODUCT ENQUIRY STATUS
# --------------------------------------------------

@app.route(
    "/company/product-enquiries/<int:enquiry_id>/status",
    methods=["POST"]
)
@admin_required
def update_product_enquiry_status(enquiry_id):
    enquiry = db.session.get(ProductEnquiry, enquiry_id)

    if enquiry is None:
        abort(404)

    allowed_statuses = {
        "New",
        "Contacted",
        "Quotation Sent",
        "Completed",
        "Cancelled",
    }

    new_status = request.form.get("status", "").strip()

    if new_status not in allowed_statuses:
        flash("Invalid enquiry status.", "error")
        return redirect(url_for("company_product_enquiries"))

    enquiry.status = new_status
    db.session.commit()

    flash(
        f"Enquiry #{enquiry.id} updated to {new_status}.",
        "success",
    )

    return redirect(url_for("company_product_enquiries"))

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
    with app.app_context():
        inspector = db.inspect(db.engine)

        quotation_columns = [
            column["name"]
            for column in inspector.get_columns("quotation")
        ]

        if "buyer_decision" not in quotation_columns:
            with db.engine.begin() as connection:
                connection.execute(
                    db.text(
                        "ALTER TABLE quotation "
                        "ADD COLUMN buyer_decision VARCHAR(20)"
                    )
                )
            print("Added buyer_decision column successfully.")
        else:
            print("buyer_decision column already exists.")

    app.run(debug=True)