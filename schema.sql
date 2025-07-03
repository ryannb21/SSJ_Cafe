CREATE DATABASE IF NOT EXISTS cafe_orders;
USE cafe_orders;

CREATE TABLE IF NOT EXISTS orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  order_time DATETIME NOT NULL,
  total_amount DECIMAL(7,2) NOT NULL
);

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
);