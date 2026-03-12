# 🧠 Finzo: Personal Finance Management System
Finzo is a comprehensive personal finance management system designed to help users track their expenses, income, and savings. It provides a user-friendly interface for managing financial data, generating reports, and setting financial goals. The system is built using Flask, a lightweight Python web framework, and utilizes a database to store user data. With Finzo, users can easily monitor their financial situation, make informed decisions, and achieve their financial objectives.

## 🚀 Features
- **User Management**: Create and manage user accounts, including password hashing and authentication
- **Expense Tracking**: Record and categorize expenses, with support for multiple expense types and tags
- **Income Management**: Set and track income, including recurring income and income history
- **Savings Tracking**: Monitor savings progress and set savings goals
- **Report Generation**: Generate PDF reports for expenses, income, and savings
- **Dashboard**: Visualize financial data with a customizable dashboard, including charts and graphs

## 🛠️ Tech Stack
* **Frontend**:  HTML, CSS, JavaScript
* **Backend**: Flask web framework, Python
* **Database**: SQLAlchemy, SQLite
* **Libraries**: Flask-SQLAlchemy, Flask-Bcrypt, reportlab, Chart.js
* **Build Tools**: pip, Python package manager

## 📦 Installation
To install Finzo, follow these steps:
1. **Prerequisites**: Install Python and pip on your system
2. **Clone the Repository**: Clone the Finzo repository using Git
3. **Install Dependencies**: Run `pip install -r requirements.txt` to install dependencies
4. **Configure the Database**: Create a SQLite database and configure the database connection in `app.py`

## 💻 Usage
1. **Run the Application**: Run `python app.py` to start the Finzo application
2. **Access the Dashboard**: Open a web browser and navigate to `http://localhost:5000` to access the Finzo dashboard
3. **Create an Account**: Create a new user account by clicking on the "Sign Up" button
4. **Log In**: Log in to your account using your username and password

## 📂 Project Structure
```markdown
personal-finance-tracker/
│
├── app.py
├── requirements.txt
├── .gitignore
│
├── instance/
│   └── database.db
│
├── templates/
│   ├── layout.html
│   ├── index.html
│   ├── dashboard.html
│   ├── analytics.html
│   ├── assistant.html
│   ├── lend_borrow.html
│   ├── login.html
│   ├── signup.html
│   ├── reports.html
│   └── settings.html
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│
└── venv/
```






