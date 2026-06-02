# RetailOps — Entity Relationship Diagram

## Mermaid ERD

```mermaid
erDiagram
    ROLE {
        int id PK
        string name
        text description
        datetime created_at
        datetime updated_at
    }
    USER {
        int id PK
        string email
        string password_hash
        string first_name
        string last_name
        int role_id FK
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    CUSTOMER {
        int id PK
        int user_id FK
        string first_name
        string last_name
        string email
        string phone
        string address_line1
        string address_line2
        string city
        string state
        string postal_code
        string country
        text notes
        datetime created_at
        datetime updated_at
    }
    PRODUCT {
        int id PK
        string sku
        string name
        text description
        int category_id FK
        string unit_of_measure
        decimal unit_price
        int low_stock_threshold
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    PRODUCTCATEGORY {
        int id PK
        string name
        text description
        int parent_category_id FK
        datetime created_at
        datetime updated_at
    }
    INVENTORYMOVEMENT {
        int id PK
        int product_id FK
        string movement_type
        int quantity
        string reference_type
        int reference_id
        text notes
        int created_by_id FK
        datetime created_at
    }
    SALESORDER {
        int id PK
        string order_number
        int customer_id FK
        string status
        decimal subtotal
        decimal tax_amount
        decimal discount_amount
        decimal total_amount
        text notes
        int created_by_id FK
        int confirmed_by_id FK
        datetime created_at
        datetime updated_at
        datetime confirmed_at
        datetime paid_at
    }
    SALESORDERITEM {
        int id PK
        int sales_order_id FK
        int product_id FK
        int quantity
        decimal unit_price
        decimal tax_rate
        decimal line_total
        datetime created_at
    }
    PAYMENT {
        int id PK
        string payment_number
        int sales_order_id FK
        decimal amount
        string payment_method
        string reference_number
        int recorded_by_id FK
        text notes
        datetime created_at
    }

    ROLE ||--o{ USER : "assigned to"
    USER |o--o{ CUSTOMER : "linked to (optional)"
    CUSTOMER ||--o{ SALESORDER : "places"
    PRODUCTCATEGORY ||--o{ PRODUCT : "classifies"
    PRODUCTCATEGORY |o--o{ PRODUCTCATEGORY : "parent of (self-ref)"
    SALESORDER ||--o{ SALESORDERITEM : "contains"
    PRODUCT ||--o{ SALESORDERITEM : "included in"
    SALESORDER ||--o{ PAYMENT : "paid via"
    SALESORDER ||--o{ INVENTORYMOVEMENT : "triggers"
    USER ||--o{ INVENTORYMOVEMENT : "recorded by"
    USER ||--o{ PAYMENT : "recorded by"
    USER ||--o{ SALESORDER : "created by / confirmed by"
```

## Cardinality Legend

| Symbol | Meaning |
|--------|---------|
| `\|\|` | Exactly one (mandatory) |
| `\|o` | Zero or one (optional) |
| `o{` | Zero or many |
| `\|{` | One or many (mandatory) |

**Reading examples:**
- `ROLE ||--o{ USER` → One Role is assigned to zero-or-many Users; each User has exactly one Role.
- `USER |o--o{ CUSTOMER` → A User is optionally linked to zero-or-many Customers; a Customer optionally has one linked User.
- `PRODUCTCATEGORY |o--o{ PRODUCTCATEGORY` → A category optionally has one parent; a parent category optionally has many child categories.

## Notes

- `INVENTORYMOVEMENT.quantity` is signed: positive = stock addition, negative = stock deduction.
- `INVENTORYMOVEMENT.reference_type` + `reference_id` form a generic (non-FK) reference to the originating record.
- `SALESORDERITEM.unit_price` is a price snapshot captured at order time, independent of the current `PRODUCT.unit_price`.
- `SALESORDER.order_number` format: `SO-YYYYMMDD-XXXX`. `PAYMENT.payment_number` format: `PAY-YYYYMMDD-XXXX`. Both auto-generated.
- `SALESORDER.confirmed_by_id` and `confirmed_at` / `paid_at` are nullable — populated only when those lifecycle events occur.
