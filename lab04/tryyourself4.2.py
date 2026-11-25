def bulk_import_products( products):
    """
    Import multiple products using upsert.

    If a product with the same SKU already exists,
    it will be updated instead of creating a duplicate.
    """

    operations = []

    for product in products:

        operations.append(
            UpdateOne(
                {"sku": product["sku"]},
                {
                    "$set": product
                },
                upsert=True
            )
        )

    if operations:
        result = col.bulk_write(operations)

        print("\n=== Bulk Import Result ===")
        print(f"  Matched:  {result.matched_count}")
        print(f"  Modified: {result.modified_count}")
        print(f"  Upserted: {result.upserted_count}")

        return result


print("\n=== Activity 4.2: Idempotent Bulk Import ===")

IMPORT_PRODUCTS = [
    {
        "sku": "A",
        "name": "Product A",
        "price": 120.00,
        "stock": 50,
        "is_active": True
    },
    {
        "sku": "B",
        "name": "Product B",
        "price": 150.00,
        "stock": 60,
        "is_active": True
    },
    {
        "sku": "G",
        "name": "Product G",
        "price": 450.00,
        "stock": 25,
        "is_active": True
    }
]

print("\n--- First Import ---")

bulk_import_products(
    col,
    IMPORT_PRODUCTS
)

print("\n--- Second Import ---")

bulk_import_products(
    col,
    IMPORT_PRODUCTS
)

print("\n=== Products After Import ===")

for doc in col.find(
    {},
    {"_id": 0}
).sort("sku"):

    print(doc)    