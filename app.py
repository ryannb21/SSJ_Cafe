from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from datetime import datetime
from flask_mail import Mail, Message
import boto3
import json
import os
import re
import time

#Setting AWS Secrets Manager Connection
def get_secret(secret_name, region_name="us-east-1"):
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    try:
        response = client.get_secret_value(SecretId=secret_name)
        secret = response['SecretString']
        return json.loads(secret)
    except client.exceptions.ResourceNotFoundException:
        print(f"Secret {secret_name} not found in region {region_name}")
        return None
    except client.exceptions.ClientError as e:
        print(f"Error retrieving secret {secret_name}: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error retrieving secret {secret_name}: {e}")
        return None


app = Flask(__name__)

#Loading secret key from AWS Secrets Manager
secret_key = get_secret(os.getenv("FLASK_CAFE_SECRET_NAME", "jordan-cafev3/flask_secret"), region_name=os.getenv("AWS_REGION", "us-east-1"))
if secret_key is None:
    raise ValueError("Failed to retrieve Flask secret key from Secrets Manager")
app.secret_key = secret_key['secret_key']


#Loading email creds securely from AWS Secrets Manager
email_secrets = get_secret(os.getenv("EMAIL_SECRET_NAME", "jordan-cafev3/emailcreds"), region_name=os.getenv("AWS_REGION", "us-east-1"))
if email_secrets is None:
    raise ValueError("Failed to retrieve email secrets from Secrets Manager")
# Gmail SMTP configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = email_secrets['email_addr']
app.config['MAIL_PASSWORD'] = email_secrets['email_password']  # You MUST create and use Gmail app password, NOT regular password here
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False

mail = Mail(app)


#Loading database creds securely from AWS Secrets Manager
db_secrets = get_secret(os.getenv("DB_SECRET_NAME", "jordan-cafev3/db_creds"), region_name=os.getenv("AWS_REGION", "us-east-1"))
if db_secrets is None:
    raise ValueError("Failed to retrieve database secrets from Secrets Manager")
#DB CONFIG
DB_CONFIG = {
    'host': db_secrets['host'],
    'user': db_secrets['user'],
    'password': db_secrets['password'], 
    'database': db_secrets['database']
}

def initialize_database():
    try:
        # Connect to MySQL without specifying database first
        cnx = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = cnx.cursor()
        # Sanitizing database name (via basic check, assuming it's from Secrets Manager)
        db_name = DB_CONFIG['database']
        if not re.match(r'^[a-zA-Z0-9_]+$', db_name):
            raise ValueError("Invalid database name")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        cursor.execute(f"USE {db_name}")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_time DATETIME NOT NULL,
                total_amount DECIMAL(7,2) NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                customer_name VARCHAR(100),
                customer_email VARCHAR(100),
                order_id INT NOT NULL,
                category VARCHAR(50),
                item_name VARCHAR(100),
                unit_price DECIMAL(5,2),
                quantity INT,
                subtotal DECIMAL(7,2),
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        """)
        cnx.commit()
    except mysql.connector.Error as e:
        print(f"Database initialization error: {e}")
        raise
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'cnx' in locals():
            cnx.close()
#Attempting initialization without failing if the whole app is not ready yet
try:
    initialize_database()
except Exception as e:
    app.logger.warning(f"Initial DB setup has failed (will retry on /health or order): {e}")


# Menu data
COFFEES = [
    {'flavor': 'French Vanilla', 'price': 3.00},
    {'flavor': 'Caramel Frappuccino', 'price': 3.75},
    {'flavor': 'Pumpkin Spice', 'price': 3.50},
    {'flavor': 'Hazelnut', 'price': 4.00},
    {'flavor': 'Mocha', 'price': 4.50},
]
DESSERTS = [
    {'name': 'Donut', 'price': 1.50},
    {'name': 'Cherry Pie', 'price': 2.75},
    {'name': 'Strawberry Cheesecake', 'price': 3.00},
    {'name': 'Cinnamon Roll', 'price': 2.50},
]

# Defining a health-check helper with backoff
def try_db_connect(max_attempts=5, base_delay=1):
    for attempt in range(1, max_attempts + 1):
        try:
            conn = mysql.connector.connect(
                host=DB_CONFIG['host'],
                user=DB_CONFIG['user'],
                password=DB_CONFIG['password'],
                database=DB_CONFIG['database'],
                connection_timeout=5,
            )
            conn.close()
            return True, "ok"
        except Exception as e:
            delay = base_delay * (2 ** (attempt - 1))
            app.logger.warning(f"DB connect attempt {attempt} failed: {e}; retrying in {delay}s")
            time.sleep(delay)
    return False, f"failed after {max_attempts} attempts"

@app.route("/health")
def health():
    db_ok, msg = try_db_connect()
    status_code = 200 if db_ok else 503
    return {"app": "running", "db": msg}, status_code


# Defining the main app route
@app.route('/')
def index():
    return render_template('index.html', coffees=COFFEES, desserts=DESSERTS)

# Sending order confirmation email to customer
def send_order_email(to_email, customer_name, items, total):
    try:
        item_lines = '\n'.join([
            f"- {name} ({qty} x ${price:.2f}) = ${subtotal:.2f}"
            for _, name, price, qty, subtotal in items
        ])

        msg = Message(
            subject="Ryan's Cafe - Order Confirmation",
            sender=app.config['MAIL_USERNAME'],
            recipients=[to_email],
            body=f"""
Hi {customer_name},

Thank you for your order at Ryan's Cafe!

Here is your order summary:
{item_lines}

Total: ${total:.2f}

We hope to serve you again soon!

Best regards,
Ryan's Cafe
            """
        )

        mail.send(msg)

    except Exception as e:
        print(f"Error sending email: {e}")
        flash("Order placed, but failed to send confirmation email.")


# Defining the order route
@app.route('/order', methods=['POST'])
def place_order():
    customer_name = request.form.get('customer_name', '').strip()
    customer_email = request.form.get('customer_email', '').strip()
    items = request.form.getlist('order_items')

    #Validating the inputs
    if not customer_name or len(customer_name) > 100:
        flash('Please provide a valid customer name (1-100 characters).')
        return redirect(url_for('index'))
    if not customer_email or not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', customer_email):
        flash('Please provide a valid email address.')
        return redirect(url_for('index'))
    if not items:
        flash('Please select at least one item.')
        return redirect(url_for('index'))

    order_details = []
    total = 0.0
    try:
        for val in items:
            parts = val.split('||')
            if len(parts) != 3:
                raise ValueError("Invalid item format")
            category, name, price_str = parts
            price = float(price_str)
            qty_key = 'qty_' + name.replace(' ', '_')
            qty = request.form.get(qty_key, '1')
            if not qty.isdigit() or int(qty) <= 0:
                raise ValueError(f"Invalid quantity for {name}")
            qty = int(qty)
            subtotal = price * qty
            total += subtotal
            order_details.append((category, name, price, qty, subtotal))
    except (ValueError, TypeError) as e:
        flash(f"Error processing order: {str(e)}")
        return redirect(url_for('index'))


    # Saving to the database
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        order_time = datetime.now()
        cursor.execute(
            "INSERT INTO orders (order_time, total_amount) VALUES (%s, %s)",
            (order_time, total)
        )
        order_id = cursor.lastrowid
        for cat, name, price, qty, subtotal in order_details:
            cursor.execute(
                "INSERT INTO order_items (customer_name, customer_email, order_id, category, item_name, unit_price, quantity, subtotal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (customer_name, customer_email, order_id, cat, name, price, qty, subtotal)
            )
        conn.commit()
    except mysql.connector.Error as e:
        print(f"Database error: {e}")
        flash("Error saving order to database.")
        return redirect(url_for('index'))
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()

    send_order_email(customer_email, customer_name, order_details, total)

    return render_template('confirmation.html', order_id=order_id, items=order_details, total=total, order_time=order_time.strftime("%Y-%m-%d %H:%M:%S"))

if __name__ == '__main__':
    app.run(debug=True)
