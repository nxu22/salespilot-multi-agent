#!/usr/bin/env python3
"""
SalesPilot seed script.

Usage:
  python seed_data.py           # create tables + load data
  python seed_data.py --verify  # also run the 4 acceptance queries
  python seed_data.py --drop    # drop all tables first (destructive)
"""

import argparse
import os
import random
import textwrap
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from faker import Faker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://salespilot:salespilot@localhost:5432/salespilot",
)

SEED = 42
fake = Faker()
Faker.seed(SEED)
random.seed(SEED)

TODAY = date.today()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id  SERIAL PRIMARY KEY,
    name        TEXT   NOT NULL UNIQUE,
    industry    TEXT,
    region      TEXT,
    created_at  DATE   NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id  SERIAL PRIMARY KEY,
    account_id  INT    NOT NULL REFERENCES accounts(account_id),
    name        TEXT   NOT NULL,
    email       TEXT   NOT NULL,
    role        TEXT
);

CREATE TABLE IF NOT EXISTS products (
    product_id  SERIAL PRIMARY KEY,
    sku         TEXT           NOT NULL UNIQUE,
    name        TEXT           NOT NULL,
    category    TEXT,
    list_price  NUMERIC(10,2)  NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    contract_id   SERIAL PRIMARY KEY,
    account_id    INT           NOT NULL REFERENCES accounts(account_id) UNIQUE,
    discount_rate NUMERIC(5,3)  NOT NULL DEFAULT 0,
    renewal_date  DATE          NOT NULL,
    doc_filename  TEXT          NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id    SERIAL PRIMARY KEY,
    account_id  INT   NOT NULL REFERENCES accounts(account_id),
    order_date  DATE  NOT NULL,
    status      TEXT  NOT NULL DEFAULT 'closed'
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id     SERIAL PRIMARY KEY,
    order_id    INT           NOT NULL REFERENCES orders(order_id),
    product_id  INT           NOT NULL REFERENCES products(product_id),
    quantity    INT           NOT NULL,
    unit_price  NUMERIC(10,2) NOT NULL  -- discounted price actually charged
);

CREATE INDEX IF NOT EXISTS idx_orders_account    ON orders(account_id);
CREATE INDEX IF NOT EXISTS idx_orders_date       ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_prod  ON order_items(product_id);
"""

READONLY_ROLE_SQL = """\
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sp_readonly') THEN
    CREATE ROLE sp_readonly WITH LOGIN PASSWORD 'readonly';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE salespilot TO sp_readonly;
GRANT USAGE ON SCHEMA public TO sp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sp_readonly;
"""

# ---------------------------------------------------------------------------
# Fixed dataset invariants
# ---------------------------------------------------------------------------

FIXED_ACCOUNTS = [
    {"name": "Acme Corp",         "industry": "Manufacturing",  "region": "Northeast"},
    {"name": "Globex",            "industry": "Technology",     "region": "West"},
    {"name": "Initech",           "industry": "Finance",        "region": "Midwest"},
    {"name": "Umbrella Ltd",      "industry": "Healthcare",     "region": "South"},
    {"name": "Soylent Systems",   "industry": "Food Tech",      "region": "West"},
    # five dormant accounts — every order will be > 90 days ago
    {"name": "Slate Rock Inc",    "industry": "Mining",         "region": "Midwest"},
    {"name": "Flintstones LLC",   "industry": "Retail",         "region": "Northeast"},
    {"name": "Spacely Sprockets", "industry": "Manufacturing",  "region": "West"},
    {"name": "Cogsworth Co",      "industry": "Technology",     "region": "South"},
    {"name": "Vandelay Ind",      "industry": "Import/Export",  "region": "Northeast"},
]

DORMANT_NAMES = {
    "Slate Rock Inc", "Flintstones LLC", "Spacely Sprockets",
    "Cogsworth Co", "Vandelay Ind",
}

FIXED_PRODUCT = {
    "sku": "PX-1000", "name": "ProMax 1000",
    "category": "Hardware", "list_price": 1250.00,
}

# Acme's contract price for PX-1000 is fixed at $1,100 (not derived from 12% discount)
ACME_PX1000_CONTRACT_PRICE = 1100.00

FIXED_CONTRACTS = {
    "Acme Corp":         {"discount_rate": 0.120, "days_until_renewal": 180, "doc": "acme_corp_msa.md"},
    "Globex":            {"discount_rate": 0.080, "days_until_renewal": 90,  "doc": "globex_msa.md"},
    "Initech":           {"discount_rate": 0.050, "days_until_renewal": 120, "doc": "initech_msa.md"},
    "Umbrella Ltd":      {"discount_rate": 0.070, "days_until_renewal": 210, "doc": "umbrella_ltd_msa.md"},
    "Soylent Systems":   {"discount_rate": 0.060, "days_until_renewal": 150, "doc": "soylent_systems_msa.md"},
    "Slate Rock Inc":    {"discount_rate": 0.040, "days_until_renewal": 365, "doc": "slate_rock_inc_msa.md"},
}

# ---------------------------------------------------------------------------
# Contract markdown generator
# ---------------------------------------------------------------------------

def _contract_md(account_name: str, discount_rate: float, renewal_date: date) -> str:
    pct = f"{discount_rate * 100:.1f}%"
    effective = TODAY - timedelta(days=365)

    px_section = ""
    if account_name == "Acme Corp":
        px_section = f"""
## Product-Specific Pricing

| SKU | Product | List Price | Contract Price |
|-----|---------|-----------|----------------|
| PX-1000 | ProMax 1000 | $1,250.00 | ${ACME_PX1000_CONTRACT_PRICE:,.2f} |

Section 3.1: Notwithstanding the standard discount rate, pricing for SKU PX-1000
(ProMax 1000) is fixed at **${ACME_PX1000_CONTRACT_PRICE:,.2f}** per unit for the
duration of this Agreement, regardless of volume ordered.
"""

    body = f"""\
# Master Services Agreement

**Customer:** {account_name}
**Effective Date:** {effective}
**Renewal Date:** {renewal_date}

## Section 1: Parties

This Master Services Agreement ("Agreement") is entered into between SalesPilot Inc.
("Vendor") and {account_name} ("Customer").

## Section 2: Pricing and Discounts

### Section 2.1: Standard Discount Rate

Customer is entitled to a standard discount of **{pct}** off the published catalog
list prices for all products and services ordered under this Agreement.

### Section 2.2: Volume Commitments

Discounts apply to all orders placed during the term of this Agreement.
{px_section}
## Section 3: Term and Renewal

This Agreement commences on the Effective Date and continues until {renewal_date},
unless terminated earlier in accordance with Section 7. Either party may renew
this Agreement by providing written notice no later than 30 days before expiration.

## Section 4: Payment Terms

Net 30 days from invoice date. Late payments accrue interest at 1.5% per month.

## Section 5: Support

Standard support (9×5) is included. Premium 24×7 support is available as an add-on.

## Section 6: Governing Law

This Agreement is governed by the laws of the State of Delaware.

## Section 7: Termination

Either party may terminate for convenience with 60 days written notice.
Termination for cause requires 10 days notice and an opportunity to cure.
"""
    return textwrap.dedent(body)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed(conn: psycopg2.extensions.connection) -> None:
    cur = conn.cursor()
    cur.execute(DDL)

    # ── accounts ──────────────────────────────────────────────────────────────
    account_ids: dict[str, int] = {}

    for acc in FIXED_ACCOUNTS:
        cur.execute(
            "INSERT INTO accounts (name, industry, region) VALUES (%s, %s, %s)"
            " ON CONFLICT (name) DO UPDATE SET industry = EXCLUDED.industry"
            " RETURNING account_id",
            (acc["name"], acc["industry"], acc["region"]),
        )
        account_ids[acc["name"]] = cur.fetchone()[0]

    industries = ["Technology", "Finance", "Healthcare", "Retail", "Manufacturing", "Logistics"]
    regions    = ["Northeast", "West", "Midwest", "South", "Pacific", "Southwest"]

    attempts = 0
    while len(account_ids) < 30 and attempts < 200:
        attempts += 1
        name = fake.company()
        if name in account_ids:
            continue
        cur.execute(
            "INSERT INTO accounts (name, industry, region) VALUES (%s, %s, %s)"
            " ON CONFLICT (name) DO NOTHING RETURNING account_id",
            (name, random.choice(industries), random.choice(regions)),
        )
        row = cur.fetchone()
        if row:
            account_ids[name] = row[0]

    # ── contacts ──────────────────────────────────────────────────────────────
    roles = ["VP Sales", "Procurement Manager", "CFO", "IT Director", "Operations Lead"]
    for acc_id in account_ids.values():
        for _ in range(random.randint(1, 3)):
            cur.execute(
                "INSERT INTO contacts (account_id, name, email, role) VALUES (%s, %s, %s, %s)",
                (acc_id, fake.name(), fake.email(), random.choice(roles)),
            )

    # ── products ──────────────────────────────────────────────────────────────
    product_ids: dict[str, int] = {}
    categories = ["Hardware", "Software", "Services", "Support", "Cloud"]

    cur.execute(
        "INSERT INTO products (sku, name, category, list_price) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (sku) DO UPDATE SET list_price = EXCLUDED.list_price"
        " RETURNING product_id",
        (FIXED_PRODUCT["sku"], FIXED_PRODUCT["name"],
         FIXED_PRODUCT["category"], FIXED_PRODUCT["list_price"]),
    )
    product_ids[FIXED_PRODUCT["sku"]] = cur.fetchone()[0]

    used_skus = {FIXED_PRODUCT["sku"]}
    suffixes  = ["Pro", "Plus", "Lite", "Suite", "Hub", "360", "X"]
    attempts  = 0
    while len(product_ids) < 50 and attempts < 500:
        attempts += 1
        sku = f"P{random.randint(1000, 9999)}"
        if sku in used_skus:
            continue
        used_skus.add(sku)
        name  = f"{fake.word().capitalize()} {random.choice(suffixes)} {random.randint(100, 999)}"
        price = round(random.uniform(200, 5000), 2)
        cur.execute(
            "INSERT INTO products (sku, name, category, list_price) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (sku) DO NOTHING RETURNING product_id",
            (sku, name, random.choice(categories), price),
        )
        row = cur.fetchone()
        if row:
            product_ids[sku] = row[0]

    all_pids   = list(product_ids.values())
    px1000_pid = product_ids[FIXED_PRODUCT["sku"]]

    # ── contracts ─────────────────────────────────────────────────────────────
    for acc_name, info in FIXED_CONTRACTS.items():
        renewal = TODAY + timedelta(days=info["days_until_renewal"])
        cur.execute(
            "INSERT INTO contracts (account_id, discount_rate, renewal_date, doc_filename)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (account_id) DO UPDATE SET discount_rate = EXCLUDED.discount_rate,"
            " renewal_date = EXCLUDED.renewal_date, doc_filename = EXCLUDED.doc_filename",
            (account_ids[acc_name], info["discount_rate"], renewal, info["doc"]),
        )

    for acc_name, acc_id in account_ids.items():
        if acc_name in FIXED_CONTRACTS:
            continue
        slug     = "".join(c if c.isalnum() else "_" for c in acc_name.lower())[:20]
        doc      = f"{slug}_msa.md"
        discount = round(random.uniform(0.02, 0.08), 3)
        renewal  = TODAY + timedelta(days=random.randint(30, 400))
        cur.execute(
            "INSERT INTO contracts (account_id, discount_rate, renewal_date, doc_filename)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING",
            (acc_id, discount, renewal, doc),
        )

    # ── orders + order_items ──────────────────────────────────────────────────
    def get_discount(acc_id: int) -> float:
        cur.execute("SELECT discount_rate FROM contracts WHERE account_id = %s", (acc_id,))
        row = cur.fetchone()
        return float(row[0]) if row else 0.0

    def get_list_price(pid: int) -> float:
        cur.execute("SELECT list_price FROM products WHERE product_id = %s", (pid,))
        return float(cur.fetchone()[0])

    def insert_order(acc_id: int, order_date: date, pids: list[int], discount: float,
                     acc_name: str) -> None:
        cur.execute(
            "INSERT INTO orders (account_id, order_date, status) VALUES (%s, %s, 'closed')"
            " RETURNING order_id",
            (acc_id, order_date),
        )
        order_id = cur.fetchone()[0]
        for pid in pids:
            if acc_name == "Acme Corp" and pid == px1000_pid:
                unit_price = ACME_PX1000_CONTRACT_PRICE
            else:
                unit_price = round(get_list_price(pid) * (1 - discount), 2)
            cur.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price)"
                " VALUES (%s, %s, %s, %s)",
                (order_id, pid, random.randint(1, 10), unit_price),
            )

    order_count = 0
    active_names = [n for n in account_ids if n not in DORMANT_NAMES]

    # Guarantee Acme has at least one recent order that includes PX-1000
    acme_id      = account_ids["Acme Corp"]
    acme_disc    = get_discount(acme_id)
    acme_pids    = random.sample([p for p in all_pids if p != px1000_pid], 2) + [px1000_pid]
    insert_order(acme_id, TODAY - timedelta(days=random.randint(5, 30)), acme_pids, acme_disc, "Acme Corp")
    order_count += 1

    # Active accounts — orders spread across last 12 months, at least one within 89 days
    for acc_name in active_names:
        if order_count >= 175:
            break
        acc_id   = account_ids[acc_name]
        discount = get_discount(acc_id)

        # One guaranteed recent order (within 89 days)
        pids = random.sample(all_pids, random.randint(1, 4))
        insert_order(acc_id, TODAY - timedelta(days=random.randint(1, 89)), pids, discount, acc_name)
        order_count += 1

        # Additional historical orders
        n_extra = random.randint(3, 8)
        for _ in range(n_extra):
            if order_count >= 175:
                break
            pids = random.sample(all_pids, random.randint(1, 4))
            insert_order(
                acc_id,
                TODAY - timedelta(days=random.randint(90, 365)),
                pids, discount, acc_name,
            )
            order_count += 1

    # Dormant accounts — all orders strictly > 90 days ago
    for acc_name in DORMANT_NAMES:
        acc_id   = account_ids[acc_name]
        discount = get_discount(acc_id)
        for _ in range(random.randint(3, 5)):
            pids = random.sample(all_pids, random.randint(1, 3))
            insert_order(
                acc_id,
                TODAY - timedelta(days=random.randint(91, 365)),
                pids, discount, acc_name,
            )
            order_count += 1

    conn.commit()
    cur.close()
    print(f"  accounts : {len(account_ids)}")
    print(f"  products : {len(product_ids)}")
    print(f"  orders   : {order_count}")


# ---------------------------------------------------------------------------
# Contract markdown files
# ---------------------------------------------------------------------------

def write_contract_docs(conn: psycopg2.extensions.connection) -> None:
    out_dir = Path("contract_docs")
    out_dir.mkdir(exist_ok=True)

    cur = conn.cursor()
    cur.execute(
        "SELECT a.name, c.discount_rate, c.renewal_date, c.doc_filename"
        " FROM contracts c JOIN accounts a ON a.account_id = c.account_id"
    )
    rows = cur.fetchall()
    cur.close()

    for acc_name, discount_rate, renewal_date, doc_filename in rows:
        content = _contract_md(acc_name, float(discount_rate), renewal_date)
        (out_dir / doc_filename).write_text(content, encoding="utf-8")

    print(f"  contract docs written : {len(rows)}")


# ---------------------------------------------------------------------------
# sp_readonly role
# ---------------------------------------------------------------------------

def create_readonly_role(conn: psycopg2.extensions.connection) -> None:
    try:
        cur = conn.cursor()
        cur.execute(READONLY_ROLE_SQL)
        conn.commit()
        cur.close()
        print("  sp_readonly role : ok")
    except Exception as exc:
        conn.rollback()
        print(f"  sp_readonly role : SKIPPED ({exc})")
        print("  → Run the SQL in CLAUDE.local.md manually as a superuser.")


# ---------------------------------------------------------------------------
# Acceptance verification
# ---------------------------------------------------------------------------

def verify(conn: psycopg2.extensions.connection) -> bool:
    cur = conn.cursor()
    passed = True

    print("\n── Acceptance queries ──────────────────────────────────────────────")

    # Q1 — dormant accounts
    cur.execute("""
        SELECT a.name
        FROM accounts a
        WHERE EXISTS (SELECT 1 FROM orders o WHERE o.account_id = a.account_id)
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.account_id = a.account_id
                AND o.order_date >= CURRENT_DATE - INTERVAL '90 days'
          )
        ORDER BY a.name
    """)
    dormant = cur.fetchall()
    status  = "PASS" if len(dormant) == 5 else "FAIL"
    if status == "FAIL":
        passed = False
    print(f"\nQ1  Dormant accounts (no order ≥ 90 days): {len(dormant)} found  [{status}]")
    for (name,) in dormant:
        print(f"    {name}")

    # Q2 — Acme discount
    cur.execute(
        "SELECT c.discount_rate, c.doc_filename"
        " FROM contracts c JOIN accounts a ON a.account_id = c.account_id"
        " WHERE a.name = 'Acme Corp'"
    )
    row = cur.fetchone()
    if row and abs(float(row[0]) - 0.120) < 0.001:
        print(f"\nQ2  Acme Corp discount: {float(row[0])*100:.1f}%  doc: {row[1]}  [PASS]")
    else:
        print(f"\nQ2  Acme Corp discount: {row}  [FAIL]")
        passed = False

    # Q3 — top 5 products by revenue this quarter
    cur.execute("""
        SELECT p.sku, p.name, SUM(oi.quantity * oi.unit_price) AS revenue
        FROM order_items oi
        JOIN products p ON p.product_id = oi.product_id
        JOIN orders   o ON o.order_id   = oi.order_id
        WHERE o.order_date >= DATE_TRUNC('quarter', CURRENT_DATE)
        GROUP BY p.product_id, p.sku, p.name
        ORDER BY revenue DESC
        LIMIT 5
    """)
    top5   = cur.fetchall()
    status = "PASS" if len(top5) > 0 else "FAIL"
    if status == "FAIL":
        passed = False
    print(f"\nQ3  Top 5 products this quarter  [{status}]")
    for rank, (sku, name, rev) in enumerate(top5, 1):
        print(f"    {rank}. {sku}  {name}  ${float(rev):,.2f}")

    # Q4 — PX-1000 catalog vs Acme contract price
    cur.execute("SELECT list_price FROM products WHERE sku = 'PX-1000'")
    catalog_row = cur.fetchone()
    cur.execute("""
        SELECT oi.unit_price
        FROM order_items oi
        JOIN orders   o  ON o.order_id   = oi.order_id
        JOIN accounts a  ON a.account_id = o.account_id
        JOIN products p  ON p.product_id = oi.product_id
        WHERE a.name = 'Acme Corp' AND p.sku = 'PX-1000'
        LIMIT 1
    """)
    contract_row = cur.fetchone()

    catalog_ok  = catalog_row  and abs(float(catalog_row[0])  - 1250.00) < 0.01
    contract_ok = contract_row and abs(float(contract_row[0]) - 1100.00) < 0.01
    status = "PASS" if (catalog_ok and contract_ok) else "FAIL"
    if status == "FAIL":
        passed = False
    cat_str  = f"${float(catalog_row[0]):,.2f}"  if catalog_row  else "missing"
    con_str  = f"${float(contract_row[0]):,.2f}" if contract_row else "missing"
    print(f"\nQ4  PX-1000  catalog={cat_str}  Acme contract={con_str}  [{status}]")

    print("\n────────────────────────────────────────────────────────────────────")
    if passed:
        print("All checks passed — data layer verified.\n")
    else:
        print("Some checks FAILED — review output above.\n")
    cur.close()
    return passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SalesPilot seed script")
    parser.add_argument("--verify", action="store_true",
                        help="Run acceptance queries after seeding")
    parser.add_argument("--drop", action="store_true",
                        help="Drop all tables before recreating (destructive)")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)

    if args.drop:
        cur = conn.cursor()
        cur.execute(
            "DROP TABLE IF EXISTS order_items, orders, contracts,"
            " products, contacts, accounts CASCADE"
        )
        conn.commit()
        cur.close()
        print("Dropped all tables.")

    print("Seeding…")
    seed(conn)

    print("Writing contract docs…")
    write_contract_docs(conn)

    print("Creating sp_readonly role…")
    create_readonly_role(conn)

    if args.verify:
        verify(conn)

    conn.close()


if __name__ == "__main__":
    main()
