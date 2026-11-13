import os
import uuid
import math
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from supabase import create_client, Client
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    session,
    current_app,
)

from math import isfinite
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from currency_service import get_exchange_rate, CurrencyServiceError
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename



# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

import os
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent


# --------------------------------------------------
# SECURITY CONFIGURATION
# --------------------------------------------------

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    if os.environ.get("RENDER"):
        raise RuntimeError(
            "SECRET_KEY must be configured in Render Environment Variables."
        )

    app.config["SECRET_KEY"] = "local-development-secret-key"


# --------------------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------------------

database_url = os.environ.get("DATABASE_URL")

if database_url:
    # Support Render PostgreSQL connection URLs
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1
        )

    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url

else:
    # Local development continues using SQLite
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + str(BASE_DIR / "marketplace.db")
    )


app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Database connection pool settings
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
}


# --------------------------------------------------
# FILE UPLOAD CONFIGURATION
# --------------------------------------------------

UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"

UPLOAD_FOLDER.mkdir(
    parents=True,
    exist_ok=True
)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB maximum


# --------------------------------------------------
# INITIALISE DATABASE
# --------------------------------------------------

db = SQLAlchemy(app)

# --------------------------------------------------
# SUPABASE — PRIVATE PAYMENT PROOF STORAGE
# --------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY"
)

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    try:
        supabase = create_client(
            SUPABASE_URL,
            SUPABASE_SERVICE_ROLE_KEY,
        )
        app.logger.info("Supabase storage client initialised.")
    except Exception:
        app.logger.exception(
            "Could not initialise Supabase storage client."
        )
else:
    app.logger.warning(
        "Supabase environment variables are not configured."
    )

PAYMENT_PROOF_BUCKET = "payments-proof"
MAX_PAYMENT_PROOF_SIZE = 5 * 1024 * 1024

ALLOWED_PAYMENT_PROOF_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
}

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
    country = db.Column(db.String(100), nullable=False, default="Lesotho")
    preferred_currency = db.Column(db.String(3), nullable=False, default="LSL")
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
    default_currency = db.Column(db.String(3), nullable=False, default="USD")
    contact_name = db.Column(db.String(120), nullable=True)
    contact_details = db.Column(db.String(150), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



# --------------------------------------------------
# POTENTIAL SUPPLIER MODEL — UNVERIFIED SOURCING LEADS
# --------------------------------------------------

class PotentialSupplier(db.Model):
    __tablename__ = "potential_supplier"

    id = db.Column(db.Integer, primary_key=True)
    supplier_name = db.Column(db.String(150), nullable=False)
    country = db.Column(db.String(100), nullable=False)
    website_url = db.Column(db.String(500), nullable=True)
    category = db.Column(db.String(100), nullable=True)
    contact_details = db.Column(db.String(200), nullable=True)
    estimated_price = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), nullable=True)
    source = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    verification_status = db.Column(
        db.String(20), nullable=False, default="Pending", index=True
    )
    sourcing_request_id = db.Column(
        db.Integer,
        db.ForeignKey("sourcing_request.id"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    sourcing_request = db.relationship("SourcingRequest")


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
    # Original supplier pricing
    supplier_original_cost = db.Column(db.Float, nullable=True)
    supplier_original_currency = db.Column(db.String(3), nullable=True)

    # Internal accounting in Lesotho Loti
    accounting_cost_lsl = db.Column(db.Float, nullable=True)

    # Original shipping estimate, before currency conversion
    shipping_estimated_original_cost = db.Column(
        db.Float,
        nullable=True,
    )
    shipping_estimated_currency = db.Column(
        db.String(3),
        nullable=True,
    )

    # Original other costs, before currency conversion
    other_costs_original = db.Column(
        db.Float,
        nullable=True,
    )
    other_costs_currency = db.Column(
        db.String(3),
        nullable=True,
    )

    # Internal accounting breakdown in LSL
    product_cost_lsl = db.Column(db.Float, nullable=True)
    sourcing_fee_lsl = db.Column(db.Float, nullable=True)
    shipping_cost_lsl = db.Column(db.Float, nullable=True)
    other_costs_lsl = db.Column(db.Float, nullable=True)
    profit_amount_lsl = db.Column(db.Float, nullable=True)
    discount_amount_lsl = db.Column(db.Float, nullable=True)
    selling_price_lsl = db.Column(db.Float, nullable=True)

    # Exchange rate used for this quotation
    exchange_rate_to_lsl = db.Column(db.Float, nullable=True)
    exchange_rate_date = db.Column(db.Date, nullable=True)
    exchange_rate_provider = db.Column(db.String(100), nullable=True)
    sourcing_fee = db.Column(db.Float, default=0, nullable=False)
    delivery_cost = db.Column(db.Float, default=0, nullable=False)
    other_costs = db.Column(db.Float, default=0, nullable=False)
    profit_amount = db.Column(db.Float, default=0, nullable=False)

    # Delivery and payment terms
    delivery_distance = db.Column(db.Float, default=0, nullable=False)
    payment_plan = db.Column(
        db.String(20),
        default="standard",
        nullable=False,
    )
    deposit_percent = db.Column(db.Float, default=0, nullable=False)
    discount_amount = db.Column(db.Float, default=0, nullable=False)

    selling_price = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False)

    # Shipping estimate and confirmation
    shipping_status = db.Column(
        db.String(20),
        default="Estimated",
        nullable=False,
    )

    # Preserve the original estimate when a confirmed cost is entered.
    shipping_estimated_cost = db.Column(
        db.Float,
        default=0,
        nullable=False,
    )

    shipping_confirmed_cost = db.Column(
        db.Float,
        nullable=True,
    )

    shipping_currency = db.Column(
        db.String(3),
        nullable=True,
    )

    freight_provider = db.Column(
        db.String(150),
        nullable=True,
    )

    shipping_reference = db.Column(
        db.String(150),
        nullable=True,
    )

    shipping_confirmed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    # Indicates whether the quotation total has been recalculated
    # using the confirmed shipping cost.
    shipping_total_recalculated = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
    )

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
# PAYMENT MODEL — UNIVERSAL RUNNER MARKETPLACE
# --------------------------------------------------

class Payment(db.Model):
    __tablename__ = "payment"

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    # The quotation this payment belongs to.
    quotation_id = db.Column(
        db.Integer,
        db.ForeignKey("quotation.id"),
        nullable=False,
        index=True,
    )

    # Amount actually received or submitted for verification.
    amount = db.Column(
        db.Float,
        nullable=False,
    )

    currency = db.Column(
        db.String(3),
        nullable=False,
    )

    # Payment details.
    payment_method = db.Column(
        db.String(50),
        nullable=False,
    )

    transaction_reference = db.Column(
        db.String(150),
        nullable=True,
    )

    # Pending, Confirmed, Failed or Refunded.
    status = db.Column(
        db.String(20),
        nullable=False,
        default="Pending",
        index=True,
    )

    notes = db.Column(
        db.Text,
        nullable=True,
    )

    # --------------------------------------------------
    # PAYMENT PROOF — PRIVATE SUPABASE STORAGE
    # --------------------------------------------------

    # Private object path inside the payment-proofs bucket.
    # Never store a public URL here.
    proof_storage_path = db.Column(
        db.String(500),
        nullable=True,
    )

    # Original filename supplied by the buyer.
    proof_original_filename = db.Column(
        db.String(255),
        nullable=True,
    )

    # Buyer who submitted the payment proof.
    submitted_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
    )

    # --------------------------------------------------
    # PAYMENT RECORDING AND VERIFICATION
    # --------------------------------------------------

    # User who recorded the payment.
    # Kept non-null to preserve the existing database design.
    recorded_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
    )

    # Administrator who verified the payment.
    confirmed_by = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
    )

    # Date the payment was recorded or submitted.
    payment_date = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # Date the administrator confirmed the payment.
    confirmed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    # --------------------------------------------------
    # DATABASE RELATIONSHIPS
    # --------------------------------------------------

    quotation = db.relationship(
        "Quotation",
        backref=db.backref(
            "payments",
            lazy=True,
        ),
    )

    recorder = db.relationship(
        "User",
        foreign_keys=[recorded_by],
    )

    confirmer = db.relationship(
        "User",
        foreign_keys=[confirmed_by],
    )

    submitter = db.relationship(
        "User",
        foreign_keys=[submitted_by],
    )

    def __repr__(self):
        return (
            f"<Payment id={self.id} "
            f"quotation_id={self.quotation_id} "
            f"amount={self.amount} "
            f"currency={self.currency} "
            f"status={self.status}>"
        )

# --------------------------------------------------
# QUOTATION EXCHANGE RATE MODEL
# --------------------------------------------------

class QuotationExchangeRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    quotation_id = db.Column(
        db.Integer,
        db.ForeignKey("quotation.id"),
        nullable=False,
    )

    from_currency = db.Column(db.String(3), nullable=False)
    to_currency = db.Column(db.String(3), nullable=False)

    # Number of target currency units for one source currency unit.
    rate = db.Column(db.Float, nullable=False)

    rate_date = db.Column(db.Date, nullable=False)
    provider = db.Column(db.String(100), nullable=False)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    quotation = db.relationship(
        "Quotation",
        backref=db.backref(
            "exchange_rates",
            lazy=True,
            cascade="all, delete-orphan",
        ),
    )

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

    # --------------------------------------------------
# UNIVERSAL RUNNER MARKETPLACE PRICING SETTINGS
# --------------------------------------------------

class PricingSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # Pricing configuration name, e.g. "default"
    name = db.Column(
        db.String(80),
        unique=True,
        nullable=False,
        default="default",
    )

    # Product markup configuration
    markup_type = db.Column(
        db.String(20),
        nullable=False,
        default="percentage",
    )
    markup_value = db.Column(
        db.Float,
        nullable=False,
        default=0,
    )

    # Separate URM service fee
    service_fee_type = db.Column(
        db.String(20),
        nullable=False,
        default="fixed",
    )
    service_fee_value = db.Column(
        db.Float,
        nullable=False,
        default=0,
    )
    service_fee_currency = db.Column(
        db.String(3),
        nullable=False,
        default="USD",
    )

    # Currency used to display the default configuration.
    # This does not convert other currencies.
    default_currency = db.Column(
        db.String(3),
        nullable=False,
        default="USD",
    )

    accounting_currency = db.Column(
    db.String(3),
    nullable=False,
    default="LSL"
    )

    # Default shipping estimate settings
    shipping_estimates_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    # Shipment prices must be confirmed separately when needed.
    require_shipping_confirmation = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

# ==========================================================
# DATABASE INITIALISATION AND SAFE COLUMN MIGRATIONS
# UNIVERSAL RUNNER MARKETPLACE
# SQLITE + POSTGRESQL COMPATIBLE
# ==========================================================


from sqlalchemy import inspect, text


def initialise_database():

    with app.app_context():

        print("Starting URM database initialisation...")

        # --------------------------------------------------
        # 1. CREATE MISSING TABLES
        # --------------------------------------------------

        # Creates missing tables without deleting existing records.
        db.create_all()

        inspector = inspect(db.engine)

        existing_tables = set(inspector.get_table_names())

        print("Database tables checked.")

        # --------------------------------------------------
        # 2. SAFE QUOTATION COLUMN MIGRATIONS
        # --------------------------------------------------

        if "quotation" in existing_tables:

            existing_columns = {
                column["name"]
                for column in inspector.get_columns("quotation")
            }

            quotation_columns = {

                "profit_amount":
                    "FLOAT NOT NULL DEFAULT 0",

                "delivery_distance":
                    "FLOAT NOT NULL DEFAULT 0",

                "payment_plan":
                    "VARCHAR(20) NOT NULL DEFAULT 'standard'",

                "deposit_percent":
                    "FLOAT NOT NULL DEFAULT 0",

                "discount_amount":
                    "FLOAT NOT NULL DEFAULT 0",

                # Shipping estimates and confirmation
                "shipping_status":
                    "VARCHAR(20) NOT NULL DEFAULT 'Estimated'",

                "shipping_estimated_cost":
                    "FLOAT NOT NULL DEFAULT 0",

                "shipping_confirmed_cost":
                    "FLOAT",

                "shipping_currency":
                    "VARCHAR(3)",

                "freight_provider":
                    "VARCHAR(150)",

                "shipping_reference":
                    "VARCHAR(150)",

                "shipping_confirmed_at":
                    "TIMESTAMP",

                "shipping_total_recalculated":
                    "BOOLEAN NOT NULL DEFAULT FALSE",
            }

            with db.engine.begin() as connection:

                for column_name, definition in quotation_columns.items():

                    if column_name not in existing_columns:

                        connection.execute(
                            text(
                                f'ALTER TABLE quotation '
                                f'ADD COLUMN "{column_name}" {definition}'
                            )
                        )

                        print(
                            f"Added quotation column: {column_name}"
                        )

        # --------------------------------------------------
        # 3. SAFE PAYMENT COLUMN MIGRATIONS
        # --------------------------------------------------

        if "payment" in existing_tables:

            existing_columns = {
                column["name"]
                for column in inspector.get_columns("payment")
            }

            payment_columns = {

                "proof_storage_path":
                    "VARCHAR(500)",

                "proof_original_filename":
                    "VARCHAR(255)",

                "submitted_by":
                    "INTEGER",
            }

            with db.engine.begin() as connection:

                for column_name, definition in payment_columns.items():

                    if column_name not in existing_columns:

                        connection.execute(
                            text(
                                f'ALTER TABLE payment '
                                f'ADD COLUMN "{column_name}" {definition}'
                            )
                        )

                        print(
                            f"Added payment column: {column_name}"
                        )

        print("Database table and column checks completed.")

        # --------------------------------------------------
        # 4. INITIALISE DEFAULT PRICING SETTINGS
        # --------------------------------------------------

        try:

            default_pricing = PricingSetting.query.filter_by(
                name="default"
            ).first()

            if default_pricing is None:

                default_pricing = PricingSetting(
                    name="default",
                    markup_type="percentage",
                    markup_value=0,
                    service_fee_type="fixed",
                    service_fee_value=0,
                    service_fee_currency="LSL",
                    default_currency="LSL",
                    shipping_estimates_enabled=True,
                    require_shipping_confirmation=True,
                )

                db.session.add(default_pricing)
                db.session.commit()

                print("Created default URM pricing settings.")

            else:

                print(
                    "Existing URM pricing settings preserved."
                )

        except Exception:

            db.session.rollback()

            raise

        print("URM database initialisation completed successfully.")


# ----------------------------------------------------------
# RUN DATABASE INITIALISATION
# ----------------------------------------------------------

print(
    "DATABASE INITIALISATION FLAG:",
    repr(os.environ.get("RUN_DB_INITIALISATION"))
)

if os.environ.get("RUN_DB_INITIALISATION", "true").lower() == "true":
    initialise_database()
else:
    app.logger.warning(
        "Automatic database initialisation is temporarily disabled."
    )


# ==========================================================
# CONSTANTS AND ACCESS CONTROL
# ==========================================================

REQUEST_STATUSES = [
    "New",
    "Sourcing",
    "Quotation Ready",
    "Approved",
    "Purchased",
    "Delivered",
    "Cancelled",
]


ADMIN_ROLES = (
    "admin",
    "super_admin",
    "company_admin",
)


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
# CATALOGUE + CUSTOM PRODUCT REQUESTS
# --------------------------------------------------

@app.route("/request-product", methods=["GET", "POST"])
@login_required
def request_product():

    if current_user.role != "buyer":
        abort(403)

    selected_product = None

    # --------------------------------------------------
    # LOAD SELECTED CATALOGUE PRODUCT
    # --------------------------------------------------

    if request.method == "GET":

        product_id = request.args.get("product_id", type=int)

        if product_id is not None:

            selected_product = Product.query.filter_by(
                id=product_id,
                published=True
            ).first_or_404()

    # --------------------------------------------------
    # PROCESS CUSTOMER REQUEST
    # --------------------------------------------------

    if request.method == "POST":

        raw_product_id = request.form.get(
            "product_id", ""
        ).strip()

        if raw_product_id:

            try:
                product_id = int(raw_product_id)

            except (TypeError, ValueError):
                abort(400, description="Invalid product reference.")

            selected_product = Product.query.filter_by(
                id=product_id,
                published=True
            ).first_or_404()

            # Catalogue product details come from the database.
            # Do not trust product details submitted by the browser.
            product_name = selected_product.name
            category = selected_product.category
            description = selected_product.description
            source_country = selected_product.country

        else:

            # Allow customers to request products not in the catalogue.
            product_name = request.form.get(
                "product_name", ""
            ).strip()

            category = request.form.get(
                "category", ""
            ).strip()

            description = request.form.get(
                "description", ""
            ).strip()

            source_country = request.form.get(
                "source_country", ""
            ).strip()

        buyer_name = request.form.get(
            "buyer_name", ""
        ).strip()

        buyer_contact = request.form.get(
            "buyer_contact", ""
        ).strip()

        delivery_country = request.form.get(
            "delivery_country", ""
        ).strip()

        currency = request.form.get(
            "currency", ""
        ).strip().upper()

        allowed_delivery_countries = {
            "Lesotho",
            "South Africa"
        }

        allowed_currencies = {
            "LSL", "ZAR", "USD", "EUR", "GBP", "CNY",
            "INR", "AED", "JPY", "AUD", "CAD", "CHF",
            "SGD", "BWP", "MZN", "ZMW"
        }

        # --------------------------------------------------
        # VALIDATE QUANTITY AND TARGET PRICE
        # --------------------------------------------------

        try:

            quantity = int(
                request.form.get("quantity", "0")
            )

            raw_price = request.form.get(
                "target_price", ""
            ).strip()

            target_price = (
                float(raw_price)
                if raw_price
                else None
            )

        except (TypeError, ValueError):

            flash(
                "Please enter a valid quantity and target price.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        # --------------------------------------------------
        # REQUIRED FIELD VALIDATION
        # --------------------------------------------------

        if not all([
            product_name,
            category,
            description,
            buyer_name,
            buyer_contact,
            source_country,
            delivery_country,
            currency
        ]):

            flash(
                "Please complete all required fields.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        if len(product_name) > 150:
            flash("Product name is too long.", "error")
            return redirect(url_for("request_product"))

        if len(category) > 80:
            flash("Invalid product category.", "error")
            return redirect(url_for("request_product"))

        if len(description) > 10000:
            flash("Product description is too long.", "error")
            return redirect(url_for("request_product"))

        if len(source_country) > 100:
            flash("Please enter a valid sourcing country.", "error")
            return redirect(url_for("request_product"))

        # --------------------------------------------------
        # DELIVERY VALIDATION
        # --------------------------------------------------

        if delivery_country not in allowed_delivery_countries:

            flash(
                "Delivery is currently available only in Lesotho and South Africa.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        # --------------------------------------------------
        # CURRENCY VALIDATION
        # --------------------------------------------------

        if currency not in allowed_currencies:

            flash(
                "Please select a supported price currency.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        # --------------------------------------------------
        # PRICE AND QUANTITY VALIDATION
        # --------------------------------------------------

        if (
            quantity < 1
            or (
                target_price is not None
                and (
                    not math.isfinite(target_price)
                    or target_price < 0
                )
            )
        ):

            flash(
                "Quantity must be at least 1 and price must be valid.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        # --------------------------------------------------
        # CREATE SOURCING REQUEST
        # --------------------------------------------------

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

        try:

            db.session.add(new_request)
            db.session.commit()

        except Exception:

            db.session.rollback()

            app.logger.exception(
                "Failed to save buyer sourcing request."
            )

            flash(
                "We could not submit your request. Please try again.",
                "error"
            )

            return redirect(
                url_for(
                    "request_product",
                    product_id=selected_product.id
                ) if selected_product
                else url_for("request_product")
            )

        # --------------------------------------------------
        # SUCCESS
        # --------------------------------------------------

        flash(
            "Your sourcing request has been submitted successfully.",
            "success"
        )

        return render_template(
            "request_success.html",
            request_id=new_request.id,
        )

    # --------------------------------------------------
    # DISPLAY REQUEST FORM
    # --------------------------------------------------

    return render_template(
        "request_product.html",
        selected_product=selected_product
    )

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
# POTENTIAL SUPPLIERS — SOURCING LEADS (ADMIN ONLY)
# --------------------------------------------------

@app.route(
    "/company/potential-suppliers",
    methods=["GET", "POST"],
)
@admin_required
def potential_suppliers():
    supported_currencies = {
        "LSL", "ZAR", "USD", "EUR", "GBP", "CNY", "INR",
        "AED", "JPY", "AUD", "CAD", "CHF", "SGD", "BWP",
        "MZN", "ZMW",
    }

    if request.method == "POST":
        supplier_name = request.form.get("supplier_name", "").strip()
        country = request.form.get("country", "").strip()
        website_url = request.form.get("website_url", "").strip()
        category = request.form.get("category", "").strip()
        contact_details = request.form.get("contact_details", "").strip()
        source = request.form.get("source", "").strip()
        notes = request.form.get("notes", "").strip()
        currency = request.form.get("currency", "").strip().upper()
        price_text = request.form.get("estimated_price", "").strip()
        request_id_text = request.form.get("sourcing_request_id", "").strip()

        if not supplier_name or len(supplier_name) > 150:
            flash("Enter a supplier name of no more than 150 characters.", "error")
            return redirect(url_for("potential_suppliers"))

        if not country or len(country) > 100:
            flash("Enter a supplier country of no more than 100 characters.", "error")
            return redirect(url_for("potential_suppliers"))

        if website_url and (
            len(website_url) > 500
            or not website_url.startswith(("https://", "http://"))
        ):
            flash("Website URL must begin with https:// or http://.", "error")
            return redirect(url_for("potential_suppliers"))

        if category and category not in MARKETPLACE_CATEGORIES:
            flash("Select a valid marketplace category.", "error")
            return redirect(url_for("potential_suppliers"))

        if len(contact_details) > 200 or len(source) > 255 or len(notes) > 5000:
            flash("One or more details exceed the allowed length.", "error")
            return redirect(url_for("potential_suppliers"))

        estimated_price = None
        if price_text:
            try:
                estimated_price = float(price_text)
            except (TypeError, ValueError):
                estimated_price = -1

            if not math.isfinite(estimated_price) or estimated_price < 0:
                flash("Enter a valid non-negative estimated price.", "error")
                return redirect(url_for("potential_suppliers"))

        if currency and currency not in supported_currencies:
            flash("Select a supported currency.", "error")
            return redirect(url_for("potential_suppliers"))

        sourcing_request_id = None
        if request_id_text:
            try:
                sourcing_request_id = int(request_id_text)
            except (TypeError, ValueError):
                sourcing_request_id = -1

            if sourcing_request_id < 1 or db.session.get(
                SourcingRequest, sourcing_request_id
            ) is None:
                flash("Select an existing sourcing request or leave it blank.", "error")
                return redirect(url_for("potential_suppliers"))

        lead = PotentialSupplier(
            supplier_name=supplier_name,
            country=country,
            website_url=website_url or None,
            category=category or None,
            contact_details=contact_details or None,
            estimated_price=estimated_price,
            currency=currency or None,
            source=source or None,
            notes=notes or None,
            verification_status="Pending",
            sourcing_request_id=sourcing_request_id,
        )

        try:
            db.session.add(lead)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Could not save potential supplier lead.")
            flash("The supplier lead could not be saved. Please try again.", "error")
            return redirect(url_for("potential_suppliers"))

        flash("Potential supplier saved as Pending. It is not yet an approved supplier.", "success")
        return redirect(url_for("potential_suppliers"))

    leads = PotentialSupplier.query.order_by(
        PotentialSupplier.created_at.desc()
    ).all()
    sourcing_requests = SourcingRequest.query.order_by(
        SourcingRequest.created_at.desc()
    ).all()

    return render_template(
        "potential_suppliers.html",
        leads=leads,
        sourcing_requests=sourcing_requests,
        categories=MARKETPLACE_CATEGORIES,
        currencies=sorted(supported_currencies),
    )


@app.route(
    "/company/potential-suppliers/<int:lead_id>/status",
    methods=["POST"],
)
@admin_required
def update_potential_supplier_status(lead_id):
    lead = db.session.get(PotentialSupplier, lead_id)
    if lead is None:
        abort(404)

    allowed_statuses = {"Pending", "Verified", "Rejected"}
    new_status = request.form.get("status", "").strip()

    if new_status not in allowed_statuses:
        abort(400, description="Invalid potential supplier status.")

    lead.verification_status = new_status
    db.session.commit()

    if new_status == "Verified":
        flash(
            "Lead marked Verified. It remains separate from approved suppliers; "
            "use Add Supplier when you are ready to register it.",
            "success",
        )
    else:
        flash(f"Potential supplier marked {new_status}.", "success")

    return redirect(url_for("potential_suppliers"))


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
@app.route(
    "/company/quotations/new",
    methods=["GET", "POST"],
)
@admin_required
def create_quotation():
    full_payment_discount_rate = 0.05
    deposit_discount_rate = 0.02

    pricing = PricingSetting.query.filter_by(name="default").first()

    if pricing is None:
        flash(
            "URM pricing settings are missing. Configure them first.",
            "error",
        )
        return redirect(url_for("pricing_settings"))

    supported_currencies = {
        "USD", "EUR", "GBP", "ZAR", "LSL",
        "CNY", "AED", "INR", "JPY", "AUD", "CAD",
    }

    if request.method == "POST":

        try:
            sourcing_request_id = int(
                request.form.get("sourcing_request_id", "")
            )
            supplier_id = int(
                request.form.get("supplier_id", "")
            )

            supplier_original_cost = float(
                request.form.get("product_cost", "")
            )

            delivery_distance = float(
                request.form.get("delivery_distance", "0")
            )

            shipping_original_cost = float(
                request.form.get("delivery_cost", "0")
            )

            other_costs_original = float(
                request.form.get("other_costs", "0")
            )

            deposit_percent = float(
                request.form.get("deposit_percent", "0")
            )

            expires_days = int(
                request.form.get("expires_days", "3")
            )

        except (TypeError, ValueError):
            flash(
                "Please enter valid quotation details.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        sourcing_request = db.session.get(
            SourcingRequest,
            sourcing_request_id,
        )

        supplier = db.session.get(
            Supplier,
            supplier_id,
        )

        if sourcing_request is None or supplier is None:
            flash(
                "Please select an existing request and supplier.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        # Buyer quotation currency.
        buyer_currency = (sourcing_request.currency or "").strip().upper()

        # Always trust the supplier's saved currency, not the browser.
        supplier_currency = (
            supplier.default_currency or ""
        ).strip().upper()

        # Independent shipping and other-cost currencies.
        shipping_currency = request.form.get(
            "shipping_currency",
            "",
        ).strip().upper()

        other_costs_currency = request.form.get(
            "other_costs_currency",
            "",
        ).strip().upper()

        currencies = [
    buyer_currency,
    supplier_currency,
    shipping_currency,
    other_costs_currency,
    pricing.service_fee_currency.strip().upper(),
    pricing.default_currency.strip().upper(),
    pricing.accounting_currency.strip().upper(),
]

        if any(code not in supported_currencies for code in currencies):
            flash(
                "One or more currencies are unsupported. "
                "Check the supplier and pricing settings.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if pricing.accounting_currency != "LSL":
            flash(
                "Internal accounting must use LSL. "
                "Please check Pricing Settings.",
                "error",
            )
            return redirect(url_for("pricing_settings"))

        if (
            supplier_original_cost != supplier_original_cost
            or shipping_original_cost != shipping_original_cost
            or other_costs_original != other_costs_original
        ):
            flash("Invalid cost amounts.", "error")
            return redirect(url_for("create_quotation"))

        amounts = [
            supplier_original_cost,
            delivery_distance,
            shipping_original_cost,
            other_costs_original,
            deposit_percent,
        ]

        if any(
            not isfinite(value) or value < 0
            for value in amounts
        ):
            flash(
                "Costs, distance and deposit must be valid "
                "non-negative amounts.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if deposit_percent > 100:
            flash(
                "Deposit percentage cannot exceed 100%.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        payment_plan = request.form.get(
            "payment_plan",
            "standard",
        ).strip().lower()

        if payment_plan not in {"standard", "full", "deposit"}:
            flash(
                "Select a valid payment plan.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if payment_plan == "deposit" and deposit_percent < 50:
            flash(
                "A deposit discount requires a deposit of at least 50%.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if payment_plan != "deposit":
            deposit_percent = 0

        if expires_days not in {1, 3, 7, 14}:
            flash(
                "Please select a valid quotation expiry.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if delivery_distance > 0 and shipping_original_cost == 0:
            flash(
                "Enter an estimated shipping cost. Distance alone "
                "cannot determine international shipping costs.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        if pricing.markup_type not in {"percentage", "fixed"}:
            flash(
                "Invalid product markup configuration.",
                "error",
            )
            return redirect(url_for("pricing_settings"))

        if pricing.service_fee_type not in {"percentage", "fixed"}:
            flash(
                "Invalid service fee configuration.",
                "error",
            )
            return redirect(url_for("pricing_settings"))

        if (
            not isfinite(float(pricing.markup_value))
            or float(pricing.markup_value) < 0
            or not isfinite(float(pricing.service_fee_value))
            or float(pricing.service_fee_value) < 0
        ):
            flash(
                "Your pricing settings contain invalid amounts.",
                "error",
            )
            return redirect(url_for("pricing_settings"))

        # --------------------------------------------------
        # EXCHANGE-RATE RETRIEVAL AND RECORDING
        # --------------------------------------------------

        rate_cache = {}

        def get_saved_rate(base_currency, target_currency):
            pair = (base_currency, target_currency)

            if pair in rate_cache:
                return rate_cache[pair]

            details = get_exchange_rate(
                base_currency,
                target_currency,
            )

            if (
                not isfinite(details["rate"])
                or details["rate"] <= 0
            ):
                raise CurrencyServiceError(
                    "The exchange-rate provider returned an invalid rate."
                )

            rate_record = QuotationExchangeRate(
                from_currency=base_currency,
                to_currency=target_currency,
                rate=details["rate"],
                rate_date=details["date"],
                provider=details["provider"],
            )

            rate_cache[pair] = {
                "details": details,
                "record": rate_record,
            }

            return rate_cache[pair]

        def convert_amount(amount, base_currency, target_currency):
            rate_info = get_saved_rate(
                base_currency,
                target_currency,
            )

            converted = amount * rate_info["details"]["rate"]

            if not isfinite(converted) or converted < 0:
                raise CurrencyServiceError(
                    "A currency conversion produced an invalid amount."
                )

            return round(converted, 2)

        try:
            # Supplier product price.
            product_cost = convert_amount(
                supplier_original_cost,
                supplier_currency,
                buyer_currency,
            )

            product_cost_lsl = convert_amount(
                supplier_original_cost,
                supplier_currency,
                "LSL",
            )

            # Shipping estimate.
            estimated_shipping = convert_amount(
                shipping_original_cost,
                shipping_currency,
                buyer_currency,
            )

            shipping_cost_lsl = convert_amount(
                shipping_original_cost,
                shipping_currency,
                "LSL",
            )

            # Other costs.
            other_costs = convert_amount(
                other_costs_original,
                other_costs_currency,
                buyer_currency,
            )

            other_costs_lsl = convert_amount(
                other_costs_original,
                other_costs_currency,
                "LSL",
            )

            # Service fee.
            if pricing.service_fee_type == "percentage":
                sourcing_fee = round(
                    product_cost * float(pricing.service_fee_value) / 100,
                    2,
                )

                sourcing_fee_lsl = round(
                    product_cost_lsl * float(pricing.service_fee_value) / 100,
                    2,
                )

            else:
                sourcing_fee = convert_amount(
                    float(pricing.service_fee_value),
                    pricing.service_fee_currency.upper(),
buyer_currency,
                )

                sourcing_fee_lsl = convert_amount(
                    float(pricing.service_fee_value),
                    pricing.service_fee_currency.upper(),
                    "LSL",
                )

            # Product markup.
            if pricing.markup_type == "percentage":
                profit_amount = round(
                    product_cost * float(pricing.markup_value) / 100,
                    2,
                )

                profit_amount_lsl = round(
                    product_cost_lsl * float(pricing.markup_value) / 100,
                    2,
                )

            else:
                profit_amount = convert_amount(
                    float(pricing.markup_value),
                    pricing.default_currency.upper(),
buyer_currency,
                )

                profit_amount_lsl = convert_amount(
                    float(pricing.markup_value),
                    pricing.default_currency.upper(),
                    "LSL",
                )

            # Discount applies only to product and shipping.
            if payment_plan == "full":
                discount_rate = full_payment_discount_rate

            elif payment_plan == "deposit":
                discount_rate = deposit_discount_rate

            else:
                discount_rate = 0.0

            discount_amount = round(
                (product_cost + estimated_shipping) * discount_rate,
                2,
            )

            discount_amount_lsl = round(
                (product_cost_lsl + shipping_cost_lsl) * discount_rate,
                2,
            )

            # Buyer-facing provisional quotation.
            provisional_total = round(
                product_cost
                + sourcing_fee
                + estimated_shipping
                + other_costs
                + profit_amount
                - discount_amount,
                2,
            )

            # Internal accounting total.
            selling_price_lsl = round(
                product_cost_lsl
                + sourcing_fee_lsl
                + shipping_cost_lsl
                + other_costs_lsl
                + profit_amount_lsl
                - discount_amount_lsl,
                2,
            )

            calculated_amounts = [
                product_cost,
                product_cost_lsl,
                sourcing_fee,
                sourcing_fee_lsl,
                estimated_shipping,
                shipping_cost_lsl,
                other_costs,
                other_costs_lsl,
                profit_amount,
                profit_amount_lsl,
                discount_amount,
                discount_amount_lsl,
                provisional_total,
                selling_price_lsl,
            ]

            if any(
                not isfinite(value) or value < 0
                for value in calculated_amounts
            ):
                raise CurrencyServiceError(
                    "The quotation calculation produced an invalid amount."
                )

            # Save the supplier-to-LSL rate in the legacy snapshot fields.
            supplier_lsl_rate = get_saved_rate(
                supplier_currency,
                "LSL",
            )["details"]

        except CurrencyServiceError as error:
            db.session.rollback()

            flash(
                f"Currency conversion failed: {error}",
                "error",
            )
            return redirect(url_for("create_quotation"))

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Unexpected error during quotation currency conversion"
            )

            flash(
                "The quotation could not be calculated. "
                "Please try again.",
                "error",
            )
            return redirect(url_for("create_quotation"))

        # --------------------------------------------------
        # CREATE THE QUOTATION
        # --------------------------------------------------

        quotation = Quotation(
            sourcing_request_id=sourcing_request.id,
            supplier_id=supplier.id,

            # Buyer-facing converted amounts.
            product_cost=product_cost,
            sourcing_fee=sourcing_fee,
            delivery_cost=estimated_shipping,
            shipping_estimated_cost=estimated_shipping,
            shipping_confirmed_cost=None,
            shipping_status="Estimated",
            shipping_currency=shipping_currency,
            shipping_confirmed_at=None,
            shipping_total_recalculated=False,

            other_costs=other_costs,
            profit_amount=profit_amount,

            # Original supplier amount.
            supplier_original_cost=supplier_original_cost,
            supplier_original_currency=supplier_currency,

            # Original shipping amount.
            shipping_estimated_original_cost=shipping_original_cost,
            shipping_estimated_currency=shipping_currency,

            # Original other costs.
            other_costs_original=other_costs_original,
            other_costs_currency=other_costs_currency,

            # Internal LSL accounting.
            accounting_cost_lsl=product_cost_lsl,
            product_cost_lsl=product_cost_lsl,
            sourcing_fee_lsl=sourcing_fee_lsl,
            shipping_cost_lsl=shipping_cost_lsl,
            other_costs_lsl=other_costs_lsl,
            profit_amount_lsl=profit_amount_lsl,
            discount_amount_lsl=discount_amount_lsl,
            selling_price_lsl=selling_price_lsl,

            # Supplier exchange-rate snapshot.
            exchange_rate_to_lsl=supplier_lsl_rate["rate"],
            exchange_rate_date=supplier_lsl_rate["date"],
            exchange_rate_provider=supplier_lsl_rate["provider"],

            delivery_distance=delivery_distance,
            payment_plan=payment_plan,
            deposit_percent=deposit_percent,
            discount_amount=discount_amount,
            selling_price=provisional_total,
            currency=buyer_currency,

            status="Draft",
            expires_at=datetime.utcnow() + timedelta(days=expires_days),
        )

        try:
            db.session.add(quotation)

            # Flush obtains the quotation ID without committing.
            db.session.flush()

            # Attach every exchange-rate record to this quotation.
            for rate_info in rate_cache.values():
                rate_record = rate_info["record"]
                rate_record.quotation_id = quotation.id
                db.session.add(rate_record)

            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Failed to save marketplace quotation and exchange rates"
            )

            flash(
                "Could not save the quotation. Please try again.",
                "error",
            )

            return redirect(url_for("create_quotation"))

        flash(
            f"Draft quotation QT-{quotation.id} saved. "
            f"Estimated buyer total: {provisional_total:.2f} {buyer_currency}. "
            f"Internal accounting total: {selling_price_lsl:.2f} LSL. "
            "Shipping must still be confirmed before the quotation can be sent.",
            "success",
        )

        return redirect(url_for("list_quotations"))

    # --------------------------------------------------
    # DISPLAY THE FORM
    # --------------------------------------------------

    requests = SourcingRequest.query.order_by(
        SourcingRequest.id.desc()
    ).all()

    suppliers = Supplier.query.order_by(
        Supplier.id.desc()
    ).all()

    return render_template(
        "quotation_form.html",
        requests=requests,
        suppliers=suppliers,
        pricing=pricing,
        supported_currencies=sorted(supported_currencies),
    )
# --------------------------------------------------
# URM ADMIN PRICING SETTINGS
# --------------------------------------------------

@app.route("/admin/pricing-settings", methods=["GET", "POST"])
@admin_required
def pricing_settings():
    pricing = PricingSetting.query.filter_by(
        name="default"
    ).first()

    # Recover safely if the default record is missing.
    if pricing is None:
        pricing = PricingSetting(
            name="default",
            markup_type="percentage",
            markup_value=0,
            service_fee_type="fixed",
            service_fee_value=0,
            service_fee_currency="USD",
            default_currency="USD",
            shipping_estimates_enabled=True,
            require_shipping_confirmation=True,
        )
        db.session.add(pricing)
        db.session.commit()

    supported_currencies = [
        "USD", "EUR", "GBP", "ZAR", "LSL",
        "CNY", "AED", "INR", "JPY", "AUD", "CAD",
    ]

    if request.method == "POST":
        try:
            markup_type = request.form.get(
                "markup_type", ""
            ).strip().lower()

            markup_value = float(
                request.form.get("markup_value", "")
            )

            service_fee_type = request.form.get(
                "service_fee_type", ""
            ).strip().lower()

            service_fee_value = float(
                request.form.get("service_fee_value", "")
            )

            default_currency = request.form.get(
                "default_currency", ""
            ).strip().upper()

            service_fee_currency = request.form.get(
                "service_fee_currency", ""
            ).strip().upper()

            shipping_estimates_enabled = (
                request.form.get("shipping_estimates_enabled") == "on"
            )

            require_shipping_confirmation = (
                request.form.get("require_shipping_confirmation") == "on"
            )

        except (TypeError, ValueError):
            flash("Enter valid numeric pricing values.", "error")
            return redirect(url_for("pricing_settings"))

        if markup_type not in {"percentage", "fixed"}:
            flash("Select a valid product markup type.", "error")
            return redirect(url_for("pricing_settings"))

        if service_fee_type not in {"percentage", "fixed"}:
            flash("Select a valid service fee type.", "error")
            return redirect(url_for("pricing_settings"))

        if (
            not math.isfinite(markup_value)
            or not math.isfinite(service_fee_value)
            or markup_value < 0
            or service_fee_value < 0
        ):
            flash("Pricing values must be finite and non-negative.", "error")
            return redirect(url_for("pricing_settings"))

        if markup_type == "percentage" and markup_value > 100:
            flash("Markup percentage cannot exceed 100%.", "error")
            return redirect(url_for("pricing_settings"))

        if (
            service_fee_type == "percentage"
            and service_fee_value > 100
        ):
            flash("Service fee percentage cannot exceed 100%.", "error")
            return redirect(url_for("pricing_settings"))

        if default_currency not in supported_currencies:
            flash("Select a supported default currency.", "error")
            return redirect(url_for("pricing_settings"))

        if service_fee_currency not in supported_currencies:
            flash("Select a supported service fee currency.", "error")
            return redirect(url_for("pricing_settings"))

        pricing.markup_type = markup_type
        pricing.markup_value = markup_value
        pricing.service_fee_type = service_fee_type
        pricing.service_fee_value = service_fee_value
        pricing.default_currency = default_currency
        pricing.service_fee_currency = service_fee_currency
        pricing.shipping_estimates_enabled = (
            shipping_estimates_enabled
        )
        pricing.require_shipping_confirmation = (
            require_shipping_confirmation
        )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to update URM pricing settings"
            )
            flash("Could not save pricing settings.", "error")
            return redirect(url_for("pricing_settings"))

        flash("URM pricing settings saved successfully.", "success")
        return redirect(url_for("pricing_settings"))

    return render_template(
        "pricing_settings.html",
        pricing=pricing,
        supported_currencies=supported_currencies,
    )

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
# Includes payment summaries for each quotation
# --------------------------------------------------

@app.route("/company/quotations")
@login_required
def list_quotations():
    # 1. Load quotations according to the user's role.
    if current_user.role in ADMIN_ROLES:
        quotations = Quotation.query.order_by(
            Quotation.created_at.desc()
        ).all()

    elif current_user.role == "buyer":
        quotations = (
            Quotation.query.join(
                SourcingRequest,
                Quotation.sourcing_request_id == SourcingRequest.id,
            )
            .filter(
                SourcingRequest.buyer_user_id == current_user.id,
                Quotation.status == "Sent",
            )
            .order_by(Quotation.created_at.desc())
            .all()
        )

    else:
        abort(403)

    # 2. Prepare the display currency.
    display_currency = request.args.get(
        "currency", "ZAR"
    ).strip().upper()

    if display_currency not in ["ZAR", "LSL"]:
        display_currency = "ZAR"

    # 3. Prepare exchange-rate information for buyers.
    exchange_rates = {}
    currency_error = None

    if current_user.role == "buyer":
        currencies_needed = {
            q.currency.upper()
            for q in quotations
            if q.currency
        }

        currencies_needed.update({
            q.shipping_currency.upper()
            for q in quotations
            if getattr(q, "shipping_currency", None)
        })

        try:
            for source_currency in currencies_needed:
                if source_currency == display_currency:
                    exchange_rates[source_currency] = {
                        "rate": 1.0,
                        "date": datetime.utcnow().date(),
                        "provider": "Same currency",
                    }
                else:
                    rate_details = get_exchange_rate(
                        source_currency,
                        display_currency,
                    )

                    exchange_rates[source_currency] = rate_details

        except CurrencyServiceError as error:
            currency_error = str(error)
            exchange_rates = {}

    # 4. Calculate payment summaries for every quotation.
    # This MUST be defined before render_template() uses it.
    payment_summaries = {}

    for quotation in quotations:
        confirmed_total, outstanding = (
            get_quotation_payment_summary(quotation)
        )

        fully_paid = outstanding <= 0.001

        if fully_paid:
            payment_status = "Fully Paid"
        elif confirmed_total > 0:
            payment_status = "Partially Paid"
        else:
            payment_status = "Unpaid"

        payment_summaries[quotation.id] = {
            "confirmed_total": confirmed_total,
            "outstanding": outstanding,
            "fully_paid": fully_paid,
            "status": payment_status,
        }

    # 5. Render the quotation list with payment summaries.
    return render_template(
        "quotations.html",
        quotations=quotations,
        ADMIN_ROLES=ADMIN_ROLES,
        now=datetime.utcnow(),
        display_currency=display_currency,
        exchange_rates=exchange_rates,
        currency_error=currency_error,
        payment_summaries=payment_summaries,
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
# CONFIRM SHIPPING COST — ADMIN ONLY
# --------------------------------------------------

@app.route(
    "/company/quotations/<int:quotation_id>/confirm-shipping",
    methods=["GET", "POST"],
)
@admin_required
def confirm_quotation_shipping(quotation_id):

    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        abort(404)

    supported_currencies = {
        "USD", "EUR", "GBP", "ZAR", "LSL",
        "CNY", "AED", "INR", "JPY", "AUD", "CAD",
    }

    if request.method == "POST":

        if quotation.status != "Draft":
            flash(
                "Shipping can only be confirmed for draft quotations.",
                "error",
            )
            return redirect(url_for("list_quotations"))

        if quotation.shipping_status == "Confirmed":
            flash(
                "Shipping has already been confirmed.",
                "error",
            )
            return redirect(url_for("list_quotations"))

        try:
            confirmed_cost = float(
                request.form.get("confirmed_cost", "")
            )
        except (TypeError, ValueError):
            flash("Enter a valid confirmed shipping cost.", "error")
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        shipping_currency = request.form.get(
            "shipping_currency", ""
        ).strip().upper()

        freight_provider = request.form.get(
            "freight_provider", ""
        ).strip()

        shipping_reference = request.form.get(
            "shipping_reference", ""
        ).strip()

        if not math.isfinite(confirmed_cost) or confirmed_cost < 0:
            flash(
                "Shipping cost must be a valid non-negative amount.",
                "error",
            )
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        if shipping_currency not in supported_currencies:
            flash("Select a supported shipping currency.", "error")
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        if not freight_provider or not shipping_reference:
            flash(
                "Enter both the freight provider and shipping reference.",
                "error",
            )
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        if len(freight_provider) > 150 or len(shipping_reference) > 150:
            flash("Freight details are too long.", "error")
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        try:
            # Convert actual shipping cost into the buyer's currency.
            buyer_rate = get_exchange_rate(
                shipping_currency,
                quotation.currency,
            )

            buyer_shipping_cost = round(
                confirmed_cost * buyer_rate["rate"],
                2,
            )

            # Convert actual shipping cost into internal LSL accounting.
            lsl_rate = get_exchange_rate(
                shipping_currency,
                "LSL",
            )

            shipping_cost_lsl = round(
                confirmed_cost * lsl_rate["rate"],
                2,
            )

            # Calculate the revised buyer quotation.
            product_cost = float(quotation.product_cost)
            sourcing_fee = float(quotation.sourcing_fee)
            other_costs = float(quotation.other_costs)
            profit_amount = float(quotation.profit_amount)

            if quotation.payment_plan == "full":
                discount_rate = 0.05
            elif quotation.payment_plan == "deposit":
                discount_rate = 0.02
            else:
                discount_rate = 0.0

            discountable_amount = (
                product_cost + buyer_shipping_cost
            )

            discount_amount = round(
                discountable_amount * discount_rate,
                2,
            )

            selling_price = round(
                product_cost
                + sourcing_fee
                + buyer_shipping_cost
                + other_costs
                + profit_amount
                - discount_amount,
                2,
            )

            # Recalculate the internal LSL accounting breakdown.
            product_cost_lsl = float(
                quotation.product_cost_lsl or 0
            )

            sourcing_fee_lsl = float(
                quotation.sourcing_fee_lsl or 0
            )

            other_costs_lsl = float(
                quotation.other_costs_lsl or 0
            )

            profit_amount_lsl = float(
                quotation.profit_amount_lsl or 0
            )

            discountable_amount_lsl = (
                product_cost_lsl + shipping_cost_lsl
            )

            discount_amount_lsl = round(
                discountable_amount_lsl * discount_rate,
                2,
            )

            selling_price_lsl = round(
                product_cost_lsl
                + sourcing_fee_lsl
                + shipping_cost_lsl
                + other_costs_lsl
                + profit_amount_lsl
                - discount_amount_lsl,
                2,
            )

            values_to_check = [
                buyer_shipping_cost,
                shipping_cost_lsl,
                discount_amount,
                selling_price,
                discount_amount_lsl,
                selling_price_lsl,
            ]

            if not all(
                math.isfinite(value) and value >= 0
                for value in values_to_check
            ):
                raise CurrencyServiceError(
                    "A converted shipping or quotation amount is invalid."
                )

            # Save the original confirmed shipping amount and currency.
            quotation.shipping_confirmed_cost = confirmed_cost
            quotation.shipping_currency = shipping_currency

            # Save the converted amount used in the buyer's quotation.
            quotation.delivery_cost = buyer_shipping_cost

            # Update internal LSL accounting.
            quotation.shipping_cost_lsl = shipping_cost_lsl
            quotation.discount_amount_lsl = discount_amount_lsl
            quotation.selling_price_lsl = selling_price_lsl

            # Update buyer-facing quotation amounts.
            quotation.discount_amount = discount_amount
            quotation.selling_price = selling_price

            quotation.freight_provider = freight_provider
            quotation.shipping_reference = shipping_reference
            quotation.shipping_status = "Confirmed"
            quotation.shipping_confirmed_at = datetime.utcnow()
            quotation.shipping_total_recalculated = True

            # Save fresh exchange-rate records for the confirmed shipping.
            db.session.add(
                QuotationExchangeRate(
                    quotation_id=quotation.id,
                    from_currency=shipping_currency,
                    to_currency=quotation.currency,
                    rate=buyer_rate["rate"],
                    rate_date=buyer_rate["date"],
                    provider=buyer_rate["provider"],
                )
            )

            db.session.add(
                QuotationExchangeRate(
                    quotation_id=quotation.id,
                    from_currency=shipping_currency,
                    to_currency="LSL",
                    rate=lsl_rate["rate"],
                    rate_date=lsl_rate["date"],
                    provider=lsl_rate["provider"],
                )
            )

            db.session.commit()

        except CurrencyServiceError as error:
            db.session.rollback()
            current_app.logger.exception(
                "Currency conversion failed during shipping confirmation"
            )
            flash(
                f"Could not confirm shipping because currency conversion "
                f"failed: {error}",
                "error",
            )
            return redirect(
                url_for(
                    "confirm_quotation_shipping",
                    quotation_id=quotation.id,
                )
            )

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed to confirm quotation shipping"
            )
            flash(
                "Shipping confirmation failed. Please try again.",
                "error",
            )
            return redirect(url_for("list_quotations"))

        flash(
            f"Shipping confirmed for QT-{quotation.id}. "
            f"Updated quotation total: {selling_price:.2f} "
            f"{quotation.currency}.",
            "success",
        )

        return redirect(url_for("list_quotations"))

    return render_template(
        "confirm_shipping.html",
        quotation=quotation,
        supported_currencies=sorted(supported_currencies),
    )
# --------------------------------------------------
# SEND QUOTATION TO BUYER — ADMIN ONLY
# --------------------------------------------------

@app.route(
    "/admin/quotations/<int:quotation_id>/send",
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

    # SHIPPING CONFIRMATION IS COMPULSORY.
    if (
        quotation.shipping_status != "Confirmed"
        or quotation.shipping_confirmed_cost is None
        or quotation.shipping_total_recalculated is not True
        or quotation.shipping_confirmed_at is None
    ):
        flash(
            "This quotation cannot be sent yet. "
            "Confirm shipping costs and recalculate the quotation first.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    # Confirm that the request belongs to a registered buyer.
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

    # Do not send expired quotations.
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
        current_app.logger.exception(
            "Could not send quotation"
        )
        flash(
            "The quotation could not be sent. Please try again.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    flash(
        f"Quotation QT-{quotation.id} has been sent to the buyer "
        "with confirmed shipping costs.",
        "success",
    )

    return redirect(url_for("list_quotations"))

# --------------------------------------------------
# PAYMENT MANAGEMENT — ADMIN ONLY
# --------------------------------------------------

def get_quotation_payment_summary(quotation):
    """Calculate confirmed payments and outstanding balance."""
    confirmed_total = sum(
        payment.amount
        for payment in quotation.payments
        if payment.status == "Confirmed"
    )

    outstanding = max(
        0.0,
        quotation.selling_price - confirmed_total,
    )

    return confirmed_total, outstanding


@app.route(
    "/admin/quotations/<int:quotation_id>/payments",
    methods=["GET", "POST"],
)

@admin_required
def manage_quotation_payments(quotation_id):
    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        abort(404)

    if request.method == "POST":
        if quotation.buyer_decision != "Approved":
            flash(
                "Payments can only be recorded for buyer-approved quotations.",
                "error",
            )
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        confirmed_total, outstanding = get_quotation_payment_summary(
            quotation
        )

        if outstanding <= 0:
            flash(
                "This quotation is already fully paid.",
                "error",
            )
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        try:
            amount = Decimal(
                request.form.get("amount", "").strip()
            )

            if not amount.is_finite() or amount <= 0:
                raise InvalidOperation

        except (InvalidOperation, ValueError):
            flash("Enter a valid positive payment amount.", "error")
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        if amount > Decimal(str(outstanding)):
            flash(
                f"Payment cannot exceed the outstanding balance of "
                f"{quotation.currency} {outstanding:.2f}.",
                "error",
            )
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        currency = request.form.get(
            "currency", ""
        ).strip().upper()

        if currency != quotation.currency:
            flash(
                f"Payment currency must be {quotation.currency}.",
                "error",
            )
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        payment_method = request.form.get(
            "payment_method", ""
        ).strip()

        transaction_reference = request.form.get(
            "transaction_reference", ""
        ).strip()

        notes = request.form.get("notes", "").strip()

        if not payment_method or len(payment_method) > 50:
            flash("Enter a valid payment method.", "error")
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        if len(transaction_reference) > 150 or len(notes) > 5000:
            flash("Payment details are too long.", "error")
            return redirect(url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            ))

        payment = Payment(
            quotation_id=quotation.id,
            amount=float(amount),
            currency=currency,
            payment_method=payment_method,
            transaction_reference=transaction_reference or None,
            notes=notes or None,
            status="Pending",
            recorded_by=current_user.id,
        )

        try:
            db.session.add(payment)
            db.session.commit()
            flash(
                "Payment recorded as Pending. Verify receipt before confirming it.",
                "success",
            )
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Could not record quotation payment"
            )
            flash("The payment could not be recorded.", "error")

        return redirect(url_for(
            "manage_quotation_payments",
            quotation_id=quotation.id,
        ))

    confirmed_total, outstanding = get_quotation_payment_summary(
        quotation
    )

    payments = Payment.query.filter_by(
        quotation_id=quotation.id
    ).order_by(
        Payment.created_at.desc()
    ).all()

    fully_paid = outstanding <= 0.001

    return render_template(
        "quotation_payments.html",
        quotation=quotation,
        payments=payments,
        confirmed_total=confirmed_total,
        outstanding=outstanding,
        fully_paid=fully_paid,
    )

# --------------------------------------------------
# BUYER — SUBMIT PAYMENT PROOF
# --------------------------------------------------

@app.route(
    "/company/quotations/<int:quotation_id>/payment-proof",
    methods=["GET", "POST"],
)
@login_required
def submit_payment_proof(quotation_id):

    if current_user.role != "buyer":
        abort(403)

    if supabase is None:
        flash(
            "Payment proof uploads are temporarily unavailable. Please contact support.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        abort(404)

    sourcing_request = db.session.get(
        SourcingRequest,
        quotation.sourcing_request_id,
    )

    if (
        sourcing_request is None
        or sourcing_request.buyer_user_id != current_user.id
    ):
        abort(403)

    if quotation.buyer_decision != "Approved":
        flash(
            "You must approve this quotation before submitting payment.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    confirmed_total, outstanding = get_quotation_payment_summary(
        quotation
    )

    pending_total = sum(
        float(payment.amount)
        for payment in quotation.payments
        if payment.status == "Pending"
    )

    available_to_submit = max(
        0.0,
        float(outstanding) - pending_total,
    )

    if available_to_submit <= 0.001:
        flash(
            "There is no outstanding amount available for another payment submission.",
            "error",
        )
        return redirect(url_for("list_quotations"))

    if request.method == "POST":

        # VALIDATE AMOUNT
        try:
            amount = Decimal(
                request.form.get("amount", "").strip()
            )

            if not amount.is_finite() or amount <= 0:
                raise InvalidOperation

        except (InvalidOperation, ValueError):
            flash("Enter a valid positive payment amount.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        if amount > Decimal(str(available_to_submit)):
            flash(
                f"Your payment cannot exceed the available amount of "
                f"{quotation.currency} {available_to_submit:.2f}.",
                "error",
            )
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        # VALIDATE PAYMENT METHOD
        allowed_methods = {
            "Bank Transfer",
            "Cash Deposit",
            "Mobile Money",
            "Card",
            "Other",
        }

        payment_method = request.form.get(
            "payment_method",
            "",
        ).strip()

        if payment_method not in allowed_methods:
            flash("Select a valid payment method.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        transaction_reference = request.form.get(
            "transaction_reference",
            "",
        ).strip()

        notes = request.form.get("notes", "").strip()

        if (
            len(transaction_reference) > 150
            or len(notes) > 5000
        ):
            flash("Your payment details are too long.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        # VALIDATE UPLOADED FILE
        # The HTML form must use name="payment_proof".

        proof_file = request.files.get("payment_proof")

        if proof_file is None or not proof_file.filename:
            flash("Please select your payment proof.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        original_filename = secure_filename(
            proof_file.filename
        )

        if not original_filename:
            flash("The uploaded filename is invalid.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        file_extension = Path(
            original_filename
        ).suffix.lower()

        allowed_types = {
            ".pdf": (
                "application/pdf",
                lambda data: data.startswith(b"%PDF-"),
            ),
            ".jpg": (
                "image/jpeg",
                lambda data: data.startswith(b"\xff\xd8\xff"),
            ),
            ".jpeg": (
                "image/jpeg",
                lambda data: data.startswith(b"\xff\xd8\xff"),
            ),
            ".png": (
                "image/png",
                lambda data: data.startswith(
                    b"\x89PNG\r\n\x1a\n"
                ),
            ),
        }

        if file_extension not in allowed_types:
            flash(
                "Only PDF, JPG, JPEG and PNG files are accepted.",
                "error",
            )
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        file_data = proof_file.read(
            MAX_PAYMENT_PROOF_SIZE + 1
        )

        if not file_data:
            flash("The uploaded file is empty.", "error")
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        if len(file_data) > MAX_PAYMENT_PROOF_SIZE:
            flash(
                "The payment proof must not exceed 5 MB.",
                "error",
            )
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        content_type, signature_check = allowed_types[
            file_extension
        ]

        if not signature_check(file_data):
            flash(
                "The uploaded file does not match its file extension.",
                "error",
            )
            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        # UPLOAD TO PRIVATE SUPABASE STORAGE

        object_name = (
            f"quotation-{quotation.id}/"
            f"{uuid.uuid4().hex}{file_extension}"
        )

        try:
           supabase.storage.from_(
    PAYMENT_PROOF_BUCKET
).upload(
    object_name,
    file_data,
    file_options={
        "content-type": content_type,
        "upsert": "false",
    },
)

        except Exception:
            current_app.logger.exception(
                "Payment proof upload failed for quotation %s",
                quotation.id,
            )

            flash(
                "Your payment proof could not be uploaded. Please try again.",
                "error",
            )

            return redirect(
                url_for(
                    "submit_payment_proof",
                    quotation_id=quotation.id,
                )
            )

        # SAVE PAYMENT RECORD

        payment = Payment(
            quotation_id=quotation.id,
            amount=float(amount),
            currency=quotation.currency,
            payment_method=payment_method,
            transaction_reference=transaction_reference or None,
            notes=notes or None,
            proof_storage_path=object_name,
            proof_original_filename=original_filename,
            submitted_by=current_user.id,
            recorded_by=current_user.id,
            status="Pending",
        )

        try:
            db.session.add(payment)
            db.session.commit()

            flash(
                "Payment proof submitted successfully. Your payment is awaiting verification.",
                "success",
            )

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Could not save payment record for quotation %s",
                quotation.id,
            )

            try:
                supabase.storage.from_(
                    PAYMENT_PROOF_BUCKET
                ).remove([object_name])

            except Exception:
                current_app.logger.exception(
                    "Could not clean up uploaded payment proof %s",
                    object_name,
                )

            flash(
                "Your payment record could not be saved. Please try again.",
                "error",
            )

        return redirect(url_for("list_quotations"))

    return render_template(
        "submit_payment_proof.html",
        quotation=quotation,
        available_to_submit=available_to_submit,
    )


# --------------------------------------------------
# ADMIN — CONFIRM PAYMENT
# --------------------------------------------------

@app.route(
    "/admin/payments/<int:payment_id>/confirm",
    methods=["POST"],
)
@admin_required
def confirm_quotation_payment(payment_id):

    payment = db.session.get(Payment, payment_id)

    if payment is None:
        abort(404)

    quotation = payment.quotation

    if payment.status != "Pending":
        flash(
            "Only pending payments can be confirmed.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            )
        )

    if quotation.buyer_decision != "Approved":
        flash(
            "The quotation must be approved by the buyer.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            )
        )

    if payment.currency != quotation.currency:
        flash(
            "Payment currency does not match the quotation.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            )
        )

    try:
        db.session.refresh(payment)
        db.session.refresh(quotation)

        if payment.status != "Pending":
            flash(
                "This payment has already been processed.",
                "error",
            )
            return redirect(
                url_for(
                    "manage_quotation_payments",
                    quotation_id=quotation.id,
                )
            )

        confirmed_total, outstanding = (
            get_quotation_payment_summary(quotation)
        )

        if outstanding <= 0.001:
            flash(
                "This quotation is already fully paid.",
                "error",
            )
            return redirect(
                url_for(
                    "manage_quotation_payments",
                    quotation_id=quotation.id,
                )
            )

        if payment.amount > outstanding + 0.001:
            flash(
                "This payment exceeds the outstanding balance. It has not been confirmed.",
                "error",
            )
            return redirect(
                url_for(
                    "manage_quotation_payments",
                    quotation_id=quotation.id,
                )
            )

        payment.status = "Confirmed"
        payment.confirmed_by = current_user.id
        payment.confirmed_at = datetime.utcnow()

        db.session.commit()

        flash("Payment confirmed successfully.", "success")

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Could not confirm quotation payment"
        )

        flash(
            "The payment could not be confirmed. Please try again.",
            "error",
        )

    return redirect(
        url_for(
            "manage_quotation_payments",
            quotation_id=quotation.id,
        )
    )


# --------------------------------------------------
# ADMIN — VIEW PRIVATE PAYMENT PROOF
# --------------------------------------------------

@app.route(
    "/admin/payments/<int:payment_id>/proof",
    methods=["GET"],
)
@admin_required
def view_payment_proof(payment_id):

    payment = db.session.get(Payment, payment_id)

    if payment is None:
        abort(404)

    if not payment.proof_storage_path:
        flash(
            "No payment proof is attached to this record.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=payment.quotation_id,
            )
        )

    if supabase is None:
        flash(
            "Private storage is currently unavailable.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=payment.quotation_id,
            )
        )

    try:
        result = supabase.storage.from_(
            PAYMENT_PROOF_BUCKET
        ).create_signed_url(
            payment.proof_storage_path,
            60,
        )

        signed_url = (
            result.get("signedURL")
            or result.get("signedUrl")
        )

        if not signed_url:
            raise ValueError(
                "Supabase did not return a signed URL."
            )

        return redirect(signed_url)

    except Exception:
        current_app.logger.exception(
            "Could not generate payment proof link for payment %s",
            payment.id,
        )

        flash(
            "Could not open the payment proof. Please try again.",
            "error",
        )

        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=payment.quotation_id,
            )
        )


# --------------------------------------------------
# ADMIN — REJECT PENDING PAYMENT
# --------------------------------------------------

@app.route(
    "/admin/payments/<int:payment_id>/reject",
    methods=["POST"],
)
@admin_required
def reject_quotation_payment(payment_id):

    payment = db.session.get(Payment, payment_id)

    if payment is None:
        abort(404)

    quotation = payment.quotation

    if payment.status != "Pending":
        flash(
            "Only pending payments can be rejected.",
            "error",
        )
        return redirect(
            url_for(
                "manage_quotation_payments",
                quotation_id=quotation.id,
            )
        )

    try:
        db.session.refresh(payment)

        if payment.status != "Pending":
            flash(
                "This payment has already been processed.",
                "error",
            )
            return redirect(
                url_for(
                    "manage_quotation_payments",
                    quotation_id=quotation.id,
                )
            )

        payment.status = "Rejected"
        db.session.commit()

        flash(
            "Payment rejected. The buyer's outstanding balance remains unchanged.",
            "success",
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Could not reject payment %s",
            payment_id,
        )

        flash(
            "The payment could not be rejected. Please try again.",
            "error",
        )

    return redirect(
        url_for(
            "manage_quotation_payments",
            quotation_id=quotation.id,
        )
    )
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
        "application": "Universal Runner Marketplace",
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
