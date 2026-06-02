# RetailOps — Workflow Diagrams

---

## Workflow A — Order-to-Payment Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Draft : Staff creates order

    Draft --> Pending : Staff saves order\n(submit for review)
    Pending --> Confirmed : Manager approves\n(stock availability verified)
    Confirmed --> Paid : Staff records payment\n(Payment entry created)
    Paid --> Shipped : Staff marks order shipped
    Shipped --> Delivered : Staff confirms delivery
    Delivered --> [*]

    Confirmed --> Cancelled : Manager or Admin\ncancels order
    Paid --> Refunded : Admin initiates refund

    Cancelled --> [*]
    Refunded --> [*]

    note right of Draft
        order_number auto-generated
        status = "draft"
    end note
    note right of Confirmed
        confirmed_by_id set
        confirmed_at set
    end note
    note right of Paid
        paid_at set
        payment_number auto-generated
    end note
```

---

## Workflow B — Inventory Update Logic

```mermaid
flowchart LR
    A([Order Event]) --> B{What event?}

    B -->|Order Confirmed| C[Deduct reserved stock\nInventoryMovement\ntype=sale, qty=negative]
    B -->|Order Cancelled\nbefore payment| D[Restore reserved stock\nInventoryMovement\ntype=return, qty=positive]
    B -->|Payment Recorded| E[Deduct committed stock\nInventoryMovement\ntype=sale, qty=negative]
    B -->|Refund Issued| F[Add stock back\nInventoryMovement\ntype=return, qty=positive]
    B -->|Purchase / Restock| G[Add to stock\nInventoryMovement\ntype=purchase, qty=positive]
    B -->|Manual Adjustment| H[Adjust stock\nInventoryMovement\ntype=adjustment, qty=±]

    C --> I{Stock ≤ low_stock_threshold?}
    D --> I
    E --> I
    F --> I
    G --> I
    H --> I

    I -->|Yes| J[🔔 Trigger Low-Stock Alert\nfor affected product]
    I -->|No| K([No Alert — Done])
    J --> K
```

---

## Workflow C — User Onboarding / Customer Registration

```mermaid
flowchart TD
    A([Guest / Staff]) --> B[Fill Registration Form\nName, Email, Phone, Address]

    B --> C{Form Valid?}
    C -->|No| D[Show Validation Errors\nHighlight fields in red]
    D --> B

    C -->|Yes| E[Account Created\nstatus = Pending\nCustomer record saved]

    E --> F{Customer needs\nsystem login?}
    F -->|Yes| G[Create linked User\nis_active = false]
    F -->|No| H[Customer-only record\nno User account]

    G --> I[Admin Reviews\nPending Accounts]
    H --> I

    I --> J{Admin Decision}
    J -->|Approve| K[Set is_active = true\nCustomer status = Active\nSend welcome notification]
    J -->|Reject| L[Mark inactive / delete\nNotify if needed]

    K --> M([Active Customer\nCan place orders])
    L --> N([Registration Rejected])
```

---

## Workflow D — Low-Stock Alert

```mermaid
flowchart LR
    A([Inventory Movement\nRecorded]) --> B[Calculate current\nstock level for product\nSUM of all InventoryMovement.quantity]

    B --> C{Stock Level ≤\nlow_stock_threshold?}

    C -->|No| D([No Action\nStock is sufficient])

    C -->|Yes| E{Stock Level = 0?}

    E -->|No — Low| F[🟠 Generate LOW STOCK Alert\nShow warning badge on product\nDisplay in Dashboard Alerts panel]
    E -->|Yes — Out| G[🔴 Generate OUT OF STOCK Alert\nShow danger badge on product\nBlock new order confirmations]

    F --> H[Show alert in\nInventory Management view\nand Dashboard summary card]
    G --> H

    H --> I{Admin / Manager\nacknowledges?}
    I -->|Yes — restock| J[Record purchase movement\ntype=purchase, qty=positive]
    I -->|No| K([Alert persists\nuntil stock replenished])

    J --> B
```
