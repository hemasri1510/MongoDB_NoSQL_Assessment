import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.connection import get_db, banner


# =========================================================
# SAMPLE PRODUCTS - 10 PRODUCTS
# =========================================================

PRODUCTS = [
    {
        "name": "Wireless Headphones",
        "price": 49.99,
        "stock": 25,
        "tags": ["electronics", "wireless"]
    },
    {
        "name": "Bluetooth Speaker",
        "price": 35.50,
        "stock": 15,
        "tags": ["electronics", "wireless"]
    },
    {
        "name": "Premium Smartwatch",
        "price": 149.99,
        "stock": 10,
        "tags": ["premium", "electronics", "wireless"]
    },
    {
        "name": "Gaming Mouse",
        "price": 29.99,
        "stock": 0,
        "tags": ["electronics", "gaming"]
    },
    {
        "name": "Mechanical Keyboard",
        "price": 79.99,
        "stock": 20,
        "tags": ["electronics", "wireless", "gaming"]
    },
    {
        "name": "USB Cable",
        "price": 12.99,
        "stock": 50,
        "tags": ["electronics", "accessory"]
    },
    {
        "name": "Premium Laptop",
        "price": 999.99,
        "stock": 5,
        "tags": ["premium", "electronics", "wireless"]
    },
    {
        "name": "Wireless Charger",
        "price": 24.99,
        "stock": 30,
        "tags": ["electronics", "wireless"]
    },
    {
        "name": "Smart TV",
        "price": 599.99,
        "stock": 0,
        "tags": ["electronics", "entertainment", "premium"]
    },
    {
        "name": "Webcam",
        "price": 45.00,
        "stock": 18,
        "tags": ["electronics", "camera"]
    }
]


# =========================================================
# CREATE COLLECTION WITH SCHEMA VALIDATION
# =========================================================

def create_products_collection(db):

    # Delete old collection if it already exists
    if "products" in db.list_collection_names():
        db.products.drop()

    validator = {
        "$jsonSchema": {
            "bsonType": "object",

            "required": [
                "name",
                "price",
                "stock",
                "tags"
            ],

            "properties": {

                "name": {
                    "bsonType": "string",
                    "description": "Name must be a string"
                },

                "price": {
                    "bsonType": [
                        "double",
                        "int",
                        "long",
                        "decimal"
                    ],
                    "minimum": 0,
                    "description": "Price must be a positive number"
                },

                "stock": {
                    "bsonType": "int",
                    "minimum": 0,
                    "description": "Stock must be a non-negative integer"
                },

                "tags": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "string"
                    },
                    "description": "Tags must be an array of strings"
                }
            }
        }
    }

    db.create_collection(
        "products",
        validator=validator,
        validationLevel="strict",
        validationAction="error"
    )

    return db.products


# =========================================================
# PAGINATION FUNCTION
# =========================================================

def paginate_products(col, page, items_per_page):

    skip = (page - 1) * items_per_page

    results = (
        col.find({}, {"_id": 0})
        .sort("name", 1)
        .skip(skip)
        .limit(items_per_page)
    )

    return list(results)


# =========================================================
# TEXT SEARCH FUNCTION USING $regex
# =========================================================

def search_by_name(col, search_string):

    results = col.find(
        {
            "name": {
                "$regex": search_string,
                "$options": "i"
            }
        },
        {
            "_id": 0,
            "name": 1,
            "price": 1,
            "stock": 1
        }
    )

    return list(results)


# =========================================================
# MAIN PROGRAM
# =========================================================

def main():

    banner("Lab 4.1: Product Query API")

    # Connect to database
    db = get_db("nosql_labs")

    # Create products collection
    col = create_products_collection(db)

    # Insert 10 products
    col.insert_many(PRODUCTS)

    print(f"[OK] Inserted {len(PRODUCTS)} products.\n")


    # =====================================================
    # 1. COMPARISON OPERATORS
    # =====================================================

    print("=== 1. Comparison: Price between 20 and 50 ===")

    results = col.find(
        {
            "price": {
                "$gte": 20,
                "$lte": 50
            }
        },
        {
            "_id": 0,
            "name": 1,
            "price": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"${r['price']:.2f}"
        )


    # -----------------------------------------------------

    print("\n=== 2. Comparison: Out of Stock ===")

    results = col.find(
        {
            "stock": 0
        },
        {
            "_id": 0,
            "name": 1,
            "stock": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"Stock = {r['stock']}"
        )


    # =====================================================
    # 2. LOGICAL OPERATORS
    # =====================================================

    print("\n=== 3. Logical: Out of Stock OR Price above 100 ===")

    results = col.find(
        {
            "$or": [
                {
                    "stock": 0
                },
                {
                    "price": {
                        "$gt": 100
                    }
                }
            ]
        },
        {
            "_id": 0,
            "name": 1,
            "price": 1,
            "stock": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"Price=${r['price']:.2f}, "
            f"Stock={r['stock']}"
        )


    # -----------------------------------------------------

    print("\n=== 4. Logical: In Stock AND tagged 'premium' ===")

    results = col.find(
        {
            "$and": [
                {
                    "stock": {
                        "$gt": 0
                    }
                },
                {
                    "tags": "premium"
                }
            ]
        },
        {
            "_id": 0,
            "name": 1,
            "stock": 1,
            "tags": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"Stock={r['stock']}, "
            f"Tags={r['tags']}"
        )


    # =====================================================
    # 3. ARRAY OPERATORS
    # =====================================================

    print("\n=== 5. Array: BOTH 'electronics' AND 'wireless' ===")

    results = col.find(
        {
            "tags": {
                "$all": [
                    "electronics",
                    "wireless"
                ]
            }
        },
        {
            "_id": 0,
            "name": 1,
            "tags": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"{r['tags']}"
        )


    # -----------------------------------------------------

    print("\n=== 6. Array: Products with exactly 2 tags ===")

    results = col.find(
        {
            "tags": {
                "$size": 2
            }
        },
        {
            "_id": 0,
            "name": 1,
            "tags": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"{r['tags']}"
        )


    # =====================================================
    # 4. PROJECTION
    # =====================================================

    print("\n=== 7. Projection: Product name and price only ===")

    results = col.find(
        {},
        {
            "_id": 0,
            "name": 1,
            "price": 1
        }
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"${r['price']:.2f}"
        )


    # =====================================================
    # 5. PAGINATION
    # =====================================================

    print("\n=== 8. Pagination: Page 1, 3 products ===")

    page_results = paginate_products(
        col,
        page=1,
        items_per_page=3
    )

    for r in page_results:
        print(
            f"  {r['name']:<25} "
            f"${r['price']:.2f}"
        )


    # -----------------------------------------------------

    print("\n=== 9. Pagination: Page 2, 3 products ===")

    page_results = paginate_products(
        col,
        page=2,
        items_per_page=3
    )

    for r in page_results:
        print(
            f"  {r['name']:<25} "
            f"${r['price']:.2f}"
        )


    # =====================================================
    # ACTIVITY 4.1 - TEXT SEARCH USING $regex
    # =====================================================

    print("\n=== 10. Text Search: Products containing 'wireless' ===")

    search_string = "wireless"

    results = search_by_name(
        col,
        search_string
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"Price=${r['price']:.2f}, "
            f"Stock={r['stock']}"
        )


    # -----------------------------------------------------
    # Test case-insensitive search
    # -----------------------------------------------------

    print("\n=== 11. Text Search: Products containing 'WIRELESS' ===")

    results = search_by_name(
        col,
        "WIRELESS"
    )

    for r in results:
        print(
            f"  {r['name']:<25} "
            f"Price=${r['price']:.2f}, "
            f"Stock={r['stock']}"
        )


    # =====================================================
    # COMPLETE
    # =====================================================

    banner("Lab 4.1 Complete")


# =========================================================
# RUN PROGRAM
# =========================================================

if __name__ == "__main__":
    main()