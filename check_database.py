from app import app, db, SourcingRequest

with app.app_context():
    print("\n=== Universal Runner Marketplace DATABASE TEST ===")

    print("Database tables:", db.metadata.tables.keys())

    total = SourcingRequest.query.count()
    print("Total saved sourcing requests:", total)

    requests = SourcingRequest.query.order_by(
        SourcingRequest.id.desc()
    ).limit(10).all()

    for item in requests:
        print("\nReference:", f"UM-{item.id}")
        print("Product:", item.product_name)
        print("Category:", item.category)
        print("Quantity:", item.quantity)
        print("Source country:", item.source_country)
        print("Delivery country:", item.delivery_country)
        print("Status:", item.status)
        print("Created:", item.created_at)

    if total > 0:
        print("\nSUCCESS: Sourcing requests are stored in the database.")
    else:
        print("\nNo requests found. Submit a test request and check again.")