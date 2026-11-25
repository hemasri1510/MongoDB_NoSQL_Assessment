"""
Lab 4.2 - Implement Upsert and Bulk Operations

Objective:
Use upserts and bulk writes to handle conditional inserts
and batch modifications.
"""

import sys
import os

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from pymongo import UpdateOne
from config.connection import get_db, reset_collection, banner


# =========================================================
# SAMPLE PRODUCTS
# =========================================================

SEED_PRODUCTS = [
    {
        "sku": "A",
        "name": "Product A",
        "price": 100.00,
        "stock": 20,
        "is_active": True
    },
    {
        "sku": "B",
        "name": "Product B",
        "price": 150.00,
        "stock": 30,
        "is_active": True
    },
    {
        "sku": "C",
        "name": "Product C",
        "price": 200.00,
        "stock": 40,
        "is_active": True
    },
    {
        "sku": "D",
        "name": "Product D",
        "price": 250.00,
        "stock": 15,
        "is_active": True
    },
    {
        "sku": "E",
        "name": "Product E",
        "price": 300.00,
        "stock": 25,
        "is_active": True
    }
]


# =========================================================
# 1. UPSERT FUNCTION
# =========================================================

def upsert_product(col, sku, price):
    """
    If SKU exists:
        Update the price.

    If SKU does not exist:
        Insert a new product with default values.
    """

    result = col.update_one(
        {"sku": sku},

        {
            "$set": {
                "price": price
            },

            "$setOnInsert": {
                "sku": sku,
                "name": "New Product",
                "stock": 0,
                "is_active": True
            }
        },

        upsert=True
    )

    if result.matched_count > 0:
        print(
            f"[UPDATED] SKU={sku}, "
            f"Price={price}"
        )
    else:
        print(
            f"[INSERTED] SKU={sku}, "
            f"Price={price}"
        )

    print(
        f"  Matched: {result.matched_count}"
    )

    print(
        f"  Modified: {result.modified_count}"
    )

    print(
        f"  Upserted: {result.upserted_id}"
    )

    return result


# =========================================================
# MAIN PROGRAM
# =========================================================

def main():

    banner("Lab 4.2: Upsert and Bulk Operations")

    # -----------------------------------------------------
    # CONNECT TO DATABASE
    # -----------------------------------------------------

    db = get_db("nosql_labs")

    # Create/reset inventory collection
    col = reset_collection(
        "nosql_labs",
        "inventory"
    )

    # Insert sample products
    col.insert_many(SEED_PRODUCTS)

    print(
        f"[OK] Inserted {len(SEED_PRODUCTS)} products.\n"
    )


    # =====================================================
    # 1. UPSERT - EXISTING PRODUCT
    # =====================================================

    print(
        "=== 1. Upsert: Existing SKU ==="
    )

    upsert_product(
        col,
        "A",
        120.00
    )

    doc = col.find_one(
        {"sku": "A"},
        {"_id": 0}
    )

    print(
        f"  Result: {doc}\n"
    )


    # =====================================================
    # 2. UPSERT - NEW PRODUCT
    # =====================================================

    print(
        "=== 2. Upsert: New SKU ==="
    )

    upsert_product(
        col,
        "F",
        500.00
    )

    doc = col.find_one(
        {"sku": "F"},
        {"_id": 0}
    )

    print(
        f"  Result: {doc}\n"
    )


    # =====================================================
    # 3. BULK WRITE
    # =====================================================

    print(
        "=== 3. Bulk Write: 5 UpdateOne Operations ==="
    )

    operations = [

        # Product A - increase stock by 10
        UpdateOne(
            {"sku": "A"},
            {
                "$inc": {
                    "stock": 10
                }
            }
        ),

        # Product B - increase stock by 10
        UpdateOne(
            {"sku": "B"},
            {
                "$inc": {
                    "stock": 10
                }
            }
        ),

        # Product C - increase stock by 10
        UpdateOne(
            {"sku": "C"},
            {
                "$inc": {
                    "stock": 10
                }
            }
        ),

        # Product D - set is_active to false
        UpdateOne(
            {"sku": "D"},
            {
                "$set": {
                    "is_active": False
                }
            }
        ),

        # Product E - set is_active to false
        UpdateOne(
            {"sku": "E"},
            {
                "$set": {
                    "is_active": False
                }
            }
        )
    ]


    # Execute all 5 operations
    bulk_result = col.bulk_write(
        operations
    )


    # =====================================================
    # 4. VERIFY BULK WRITE RESULT
    # =====================================================

    print("\n=== Bulk Write Result ===")

    print(
        f"  Matched Documents:  "
        f"{bulk_result.matched_count}"
    )

    print(
        f"  Modified Documents: "
        f"{bulk_result.modified_count}"
    )

    print(
        f"  Upserted Documents: "
        f"{bulk_result.upserted_count}"
    )

    print(
        f"  Upserted IDs:       "
        f"{bulk_result.upserted_ids}"
    )


    # =====================================================
    # 5. FINAL INVENTORY
    # =====================================================

    print("\n=== Final Inventory State ===")

    for doc in col.find(
        {},
        {"_id": 0}
    ).sort("sku"):

        print(
            f"  SKU={doc['sku']:<5} "
            f"Name={doc['name']:<15} "
            f"Price=${doc['price']:<8.2f} "
            f"Stock={doc['stock']:<3} "
            f"Active={doc['is_active']}"
        )


    # =====================================================
    # 6. VERIFY INDIVIDUAL PRODUCTS
    # =====================================================

    print("\n=== Verification ===")

    print("Products A, B and C:")
    print("  Stock increased by 10.")

    print("\nProducts D and E:")
    print("  is_active changed to False.")

    print("\nProduct F:")
    print("  Inserted using upsert with default values.")


    banner("Lab 4.2 Complete")


# =========================================================
# RUN PROGRAM
# =========================================================

if __name__ == "__main__":
    main()