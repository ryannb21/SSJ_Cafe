from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from datetime import datetime
from flask_mail import Mail, Message
import boto3
import json

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
app.secret_key = 'change_this_to_a_secure_key' #REVIEW THIS


#Loading email creds securely from AWS Secrets Manager
email_secrets = get_secret("jordan-cafev2/emailcreds", region_name="us-east-1")  #Review concerns with hardcoding this
if email_secrets is None:
    raise ValueError("Failed to retrieve email secrets from Secrets Manager")
# Gmail SMTP configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = email_secrets['email_addr']         # replace this
app.config['MAIL_PASSWORD'] = email_secrets['email_password']      # use Gmail app password, NOT regular password
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False

mail = Mail(app)


#Loading database creds securely from AWS Secrets Manager
db_secrets = get_secret("jordan-cafev2/db_creds", region_name="us-east-1")
if db_secrets is None:
    raise ValueError("Failed to retrieve database secrets from Secrets Manager")
#DB CONFIG
DB_CONFIG = {
    'host': db_secrets['host'],
    'user': db_secrets['user'],
    'password': db_secrets['password'], #Change to ! for rds
    'database': db_secrets['database']
}

def initialize_database():
    # Connect to MySQL without specifying database first
    cnx = mysql.connector.connect(
        host=DB_CONFIG['host'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password']
    )
    cursor = cnx.cursor()

    # Create the database if it doesn't exist
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
    cursor.execute(f"USE {DB_CONFIG['database']}")

    # Create tables if they don't exist (using your schema)
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
    cursor.close()
    cnx.close()

initialize_database()


# Menu data
COFFEES = [
    {'flavor': 'French Vanilla', 'price': 3.00},
    {'flavor': 'Caramel Macchiato', 'price': 3.75},
    {'flavor': 'Pumpkin Spice', 'price': 3.50},
    {'flavor': 'Hazelnut', 'price': 4.00},
    {'flavor': 'Mocha', 'price': 4.50},
]
DESSERTS = [
    {'name': 'Donut', 'price': 1.50},
    {'name': 'Cherry Pie', 'price': 2.75},
    {'name': 'Cheesecake', 'price': 3.00},
    {'name': 'Cinnamon Roll', 'price': 2.50},
]

# Defining the main app route
@app.route('/')
def index():
    return render_template('index.html', coffees=COFFEES, desserts=DESSERTS)

# Send order confirmation email to customer
def send_order_email(to_email, customer_name, items, total):
    item_lines = '\n'.join([f"- {name} ({qty} x ${price:.2f}) = ${subtotal:.2f}" for _, name, price, qty, subtotal in items])
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

# Defining the order route
@app.route('/order', methods=['POST'])
def place_order():
    customer_name = request.form.get('customer_name')
    customer_email = request.form.get('customer_email')
    items = request.form.getlist('order_items')

    if not items:
        flash('Please select at least one item.')
        return redirect(url_for('index'))

    order_details = []
    total = 0.0
    for val in items:
        category, name, price_str = val.split('||')
        price = float(price_str)
        qty_key = 'qty_' + name.replace(' ', '_')
        qty = int(request.form.get(qty_key, 1))
        subtotal = price * qty
        total += subtotal
        order_details.append((category, name, price, qty, subtotal))


    # Saving to the database
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
    cursor.close()
    conn.close()

    send_order_email(customer_email, customer_name, order_details, total)

    return render_template('confirmation.html', order_id=order_id, items=order_details, total=total, order_time=order_time.strftime("%Y-%m-%d %H:%M:%S"))

if __name__ == '__main__'
