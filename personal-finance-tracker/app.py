# venv\Scripts\activate  (step 1)
# python app.py           (step 2)
# pip install reportlab   (needed once for PDF export)

from flask import (Flask, render_template, request, redirect,
                   url_for, session, Response, make_response)
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from collections import defaultdict
from datetime import datetime, timedelta, date
import csv, io

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

app = Flask(__name__)
app.config['SECRET_KEY']                     = 'personal_budget_secret_key'
app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db     = SQLAlchemy(app)
bcrypt = Bcrypt(app)


# ══════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════

class User(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    email        = db.Column(db.String(120), unique=True, nullable=False)
    password     = db.Column(db.String(200), nullable=False)
    income       = db.Column(db.Float,  default=0)
    budget       = db.Column(db.Float,  default=0)
    savings_goal      = db.Column(db.Float,  default=0)
    occasional_fund   = db.Column(db.Float,  default=0)


class Expense(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    amount   = db.Column(db.Float,       nullable=False)
    category = db.Column(db.String(100), nullable=False)
    date     = db.Column(db.DateTime,    default=datetime.now)
    user_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class IncomeLog(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    amount  = db.Column(db.Float,       nullable=False)
    label   = db.Column(db.String(100), default="Bonus")
    date    = db.Column(db.DateTime,    default=datetime.now)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


class Profile(db.Model):
    """Stores extra user profile info (separate from User to keep it clean)."""
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    full_name = db.Column(db.String(120), default='')
    phone     = db.Column(db.String(30),  default='')
    avatar    = db.Column(db.String(10),  default='😊')
    currency  = db.Column(db.String(5),   default='₹')


class OccasionalSpend(db.Model):
    """Tracks spending from the occasional/emergency fund (festivals, medical, travel, gifts)."""
    id       = db.Column(db.Integer, primary_key=True)
    amount   = db.Column(db.Float,       nullable=False)
    label    = db.Column(db.String(100), nullable=False, default="Occasional")
    category = db.Column(db.String(50),  nullable=False, default="Other")
    date     = db.Column(db.DateTime,    default=datetime.now)
    user_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self): return f"<OccasionalSpend {self.label}: {self.amount}>"


class LendBorrow(db.Model):
    """Tracks money lent to others or borrowed from others."""
    id           = db.Column(db.Integer, primary_key=True)
    type         = db.Column(db.String(10),  nullable=False)   # 'lend' or 'borrow'
    person_name  = db.Column(db.String(120), nullable=False)
    phone        = db.Column(db.String(30),  default='')
    address      = db.Column(db.String(250), default='')
    amount       = db.Column(db.Float,       nullable=False)
    reason       = db.Column(db.String(250), default='')
    interest_pct = db.Column(db.Float,       default=0)        # annual % interest, 0 = none
    date         = db.Column(db.DateTime,    default=datetime.now)
    due_date     = db.Column(db.DateTime,    nullable=True)
    status       = db.Column(db.String(20),  default='active') # active | settled
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    repayments   = db.relationship('Repayment', backref='loan', lazy=True, cascade='all,delete-orphan')

    @property
    def total_repaid(self):
        return sum(r.amount for r in self.repayments)

    @property
    def outstanding(self):
        return round(max(0, self.amount - self.total_repaid), 2)

    @property
    def is_overdue(self):
        if self.due_date and self.status == 'active':
            return datetime.now() > self.due_date
        return False


class Repayment(db.Model):
    """Partial or full repayment against a LendBorrow record."""
    id          = db.Column(db.Integer, primary_key=True)
    loan_id     = db.Column(db.Integer, db.ForeignKey('lend_borrow.id'), nullable=False)
    amount      = db.Column(db.Float,   nullable=False)
    note        = db.Column(db.String(200), default='')
    date        = db.Column(db.DateTime, default=datetime.now)


# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════

def _get_or_create_profile(user_id):
    p = Profile.query.filter_by(user_id=user_id).first()
    if not p:
        p = Profile(user_id=user_id)
        db.session.add(p)
        db.session.commit()
    return p


def _date_range_from_params(request_args):
    today = date.today()
    quick = request_args.get("quick", "")
    if quick == "this_month":
        fd, td = date(today.year, today.month, 1), today
    elif quick == "last_month":
        first = date(today.year, today.month, 1)
        last  = first - timedelta(days=1)
        fd, td = date(last.year, last.month, 1), last
    elif quick == "last_7":
        fd, td = today - timedelta(days=7), today
    elif quick == "this_year":
        fd, td = date(today.year, 1, 1), today
    elif quick == "all":
        fd, td = date(2000, 1, 1), today
    else:
        try:    fd = datetime.strptime(request_args.get("from",""), "%Y-%m-%d").date()
        except: fd = date(today.year, today.month, 1)
        try:    td = datetime.strptime(request_args.get("to",""), "%Y-%m-%d").date()
        except: td = today
    from_dt = datetime(fd.year, fd.month, fd.day, 0, 0, 0)
    to_dt   = datetime(td.year, td.month, td.day, 23, 59, 59)
    return from_dt, to_dt, fd.strftime("%Y-%m-%d"), td.strftime("%Y-%m-%d"), quick


def _fetch_report_expenses(user_id, from_dt, to_dt):
    return Expense.query.filter(
        Expense.user_id == user_id,
        Expense.date    >= from_dt,
        Expense.date    <= to_dt
    ).order_by(Expense.date.asc()).all()


def _get_expenses(user_id, period):
    now = datetime.now()
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = datetime(now.year, now.month, 1)
    elif period == "year":
        start = datetime(now.year, 1, 1)
    else:
        return Expense.query.filter_by(user_id=user_id).all()
    return Expense.query.filter(
        Expense.user_id == user_id,
        Expense.date    >= start
    ).all()


def _build_trend(expenses, period):
    if not expenses: return [], []
    by_day = defaultdict(float)
    for exp in expenses:
        key = exp.date.strftime("%d %b") if period in ("week","month") else exp.date.strftime("%b %Y")
        by_day[key] += exp.amount
    seen = []
    for exp in sorted(expenses, key=lambda e: e.date):
        key = exp.date.strftime("%d %b") if period in ("week","month") else exp.date.strftime("%b %Y")
        if key not in seen: seen.append(key)
    return seen, [round(by_day[k], 2) for k in seen]


def _health_score(savings_rate, budget_used_pct, goal_progress):
    s = min(savings_rate / 50 * 40, 40)
    b = max(0, (1 - budget_used_pct / 100) * 40)
    g = min(goal_progress / 100 * 20, 20)
    score = int(round(s + b + g))
    if score >= 75: return score,"#22C55E","Excellent 🌟","You are managing your finances very well. Keep it up!"
    if score >= 50: return score,"#F59E0B","Good 👍","Solid financial health. A few tweaks could make it great."
    if score >= 25: return score,"#F97316","Fair ⚠️","Some areas need attention. Review your budget and savings."
    return score,"#EF4444","Needs Work 🔴","Consider cutting expenses and setting a savings goal."


def _generate_insights(user, category_totals, total_expense,
                       savings_rate, budget_used_pct, top_category, period):
    insights = []
    if savings_rate >= 20:
        insights.append({"icon":"🎉","type":"good","message":f"You're saving {savings_rate:.1f}% of your income — above the recommended 20%. Well done!"})
    elif savings_rate > 0:
        insights.append({"icon":"💡","type":"info","message":f"Your savings rate is {savings_rate:.1f}%. Aim for at least 20% to build a strong financial cushion."})
    else:
        insights.append({"icon":"⚠️","type":"bad","message":"You're currently not saving anything. Try reducing your biggest expense category."})
    if user.budget > 0 and budget_used_pct > 90:
        insights.append({"icon":"🚨","type":"bad","message":f"You've used {budget_used_pct:.0f}% of your budget. Slow down spending to avoid going over."})
    elif user.budget > 0 and budget_used_pct > 70:
        insights.append({"icon":"⚠️","type":"warn","message":f"Budget is {budget_used_pct:.0f}% used. Be mindful with remaining spending this period."})
    elif user.budget > 0:
        insights.append({"icon":"✅","type":"good","message":f"Only {budget_used_pct:.0f}% of your budget used. Great spending discipline!"})
    if top_category and top_category != "—" and total_expense > 0:
        top_pct = (category_totals.get(top_category, 0) / total_expense) * 100
        if top_pct > 50:
            insights.append({"icon":"📌","type":"warn","message":f"'{top_category}' accounts for {top_pct:.0f}% of your spending. Consider if this is intentional."})
    if len(category_totals) >= 4:
        insights.append({"icon":"📊","type":"info","message":f"You're tracking {len(category_totals)} categories — good habit for spotting spending patterns."})
    return insights


# ══════════════════════════════════════════════════════
#  CORE ROUTES
# ══════════════════════════════════════════════════════

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        email, password = request.form.get("email"), request.form.get("password")
        if User.query.filter_by(email=email).first():
            return render_template("signup.html", error="This email is already registered. Please log in.")
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        db.session.add(User(email=email, password=hashed))
        db.session.commit()
        return redirect(url_for('login'))
    return render_template("signup.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email, password = request.form.get("email"), request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            session['user_id']    = user.id
            session['user_email'] = user.email
            return redirect(url_for('dashboard'))
        return render_template("login.html", error="Invalid email or password. Please try again.")
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    user        = db.session.get(User, session['user_id'])
    filter_type = request.args.get("filter","all")
    now         = datetime.now()

    if filter_type == "daily":
        start    = datetime(now.year, now.month, now.day)
        expenses = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=start).all()
    elif filter_type == "weekly":
        expenses = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=now-timedelta(days=7)).all()
    elif filter_type == "monthly":
        start    = datetime(now.year, now.month, 1)
        expenses = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=start).all()
    else:
        expenses = Expense.query.filter_by(user_id=session['user_id']).all()

    total_expense = sum(e.amount for e in expenses)
    savings       = user.income - total_expense
    goal_progress = max(0,min(100,(savings/user.savings_goal)*100)) if user.savings_goal>0 else 0
    overspend     = user.budget>0 and total_expense>user.budget
    personality   = "No Data"
    if user.budget > 0:
        if   total_expense < 0.7*user.budget: personality = "Happy 😄"
        elif total_expense <= user.budget:    personality = "Normal 😐"
        else:                                 personality = "Sad 😟"

    category_totals = defaultdict(float)
    for exp in expenses: category_totals[exp.category] += exp.amount

    category_alerts = []
    for cat,amt in category_totals.items():
        if user.budget>0 and amt>0.4*user.budget: category_alerts.append(f"High spending in {cat}!")
        elif total_expense>0 and amt>0.5*total_expense: category_alerts.append(f"{cat} dominates your spending!")

    assistant_message = "You're doing fine. Keep tracking your expenses regularly!"
    if overspend:          assistant_message = "You have exceeded your budget. Try reducing non-essential expenses."
    elif personality=="Sad 😟":    assistant_message = "Your spending is higher than your budget. Consider reviewing your categories."
    elif personality=="Normal 😐": assistant_message = "You are close to your budget. Spend carefully for the rest of the period."
    elif personality=="Happy 😄":  assistant_message = "Great job! You are managing your expenses very well. Keep it up!"
    if user.savings_goal>0 and goal_progress>=100: assistant_message = "🎉 Congratulations! You have achieved your savings goal!"

    income_logs = IncomeLog.query.filter_by(user_id=session['user_id']).order_by(IncomeLog.date.desc()).limit(5).all()

    # Occasional fund
    occ_spends     = OccasionalSpend.query.filter_by(user_id=session['user_id']).order_by(OccasionalSpend.date.desc()).all()
    occ_spent      = round(sum(s.amount for s in occ_spends), 2)
    occ_remaining  = round(max(0, user.occasional_fund - occ_spent), 2)
    occ_used_pct   = round(min((occ_spent / user.occasional_fund) * 100, 100), 1) if user.occasional_fund > 0 else 0
    occ_recent     = occ_spends[:5]

    return render_template("dashboard.html",
        email=session['user_email'], expenses=expenses, total_expense=total_expense,
        income=user.income, savings=savings, budget=user.budget,
        savings_goal=user.savings_goal, goal_progress=goal_progress,
        overspend=overspend, personality=personality,
        category_totals=dict(category_totals), current_filter=filter_type,
        category_alerts=category_alerts, assistant_message=assistant_message,
        income_logs=income_logs,
        occasional_fund=user.occasional_fund,
        occ_spent=occ_spent,
        occ_remaining=occ_remaining,
        occ_used_pct=occ_used_pct,
        occ_recent=occ_recent,
    )


@app.route("/analytics")
def analytics():
    if 'user_id' not in session: return redirect(url_for('login'))
    user   = db.session.get(User, session['user_id'])
    period = request.args.get("period","month")
    expenses      = _get_expenses(session['user_id'], period)
    total_expense = sum(e.amount for e in expenses)
    savings       = user.income - total_expense

    category_totals = defaultdict(float)
    for exp in expenses: category_totals[exp.category] += exp.amount
    category_totals = dict(sorted(category_totals.items(), key=lambda x:x[1], reverse=True))

    top_expenses           = sorted(expenses, key=lambda e:e.amount, reverse=True)[:10]
    trend_labels, trend_values = _build_trend(expenses, period)
    days_in_period      = max(len(set(e.date.date() for e in expenses)),1)
    avg_daily_spend     = total_expense/days_in_period if expenses else 0
    savings_rate        = max(0,(savings/user.income*100)) if user.income>0 else 0
    budget_used_pct     = min((total_expense/user.budget*100),100) if user.budget>0 else 0
    top_category        = max(category_totals, key=category_totals.get) if category_totals else "—"
    goal_progress       = max(0,min(100,(savings/user.savings_goal)*100)) if user.savings_goal>0 else 0

    months_to_goal = None
    if user.savings_goal>0 and savings<user.savings_goal:
        ms = user.income - (total_expense/max(days_in_period,1)*30)
        if ms>0: months_to_goal = max(1,int((user.savings_goal-savings)/ms)+1)

    health_score,health_color,health_label,health_desc = _health_score(savings_rate,budget_used_pct,goal_progress)
    health_dash = int((health_score/100)*314)

    income_breakdown = {}
    if user.income > 0:
        all_logs    = IncomeLog.query.filter_by(user_id=session['user_id']).all()
        total_bonus = sum(l.amount for l in all_logs)
        base_salary = max(0, user.income-total_bonus)
        if base_salary>0: income_breakdown["Base Salary"] = round(base_salary,2)
        for log in all_logs:
            lbl = log.label.strip().title()
            income_breakdown[lbl] = income_breakdown.get(lbl,0) + log.amount

    insights = _generate_insights(user,category_totals,total_expense,savings_rate,budget_used_pct,top_category,period)

    return render_template("analytics.html",
        email=session['user_email'], period=period, income=user.income,
        total_expense=total_expense, savings=savings, budget=user.budget,
        savings_goal=user.savings_goal, savings_rate=savings_rate,
        budget_used_pct=budget_used_pct, avg_daily_spend=avg_daily_spend,
        top_category=top_category, total_transactions=len(expenses),
        goal_progress=goal_progress, months_to_goal=months_to_goal,
        health_score=health_score, health_color=health_color,
        health_label=health_label, health_desc=health_desc, health_dash=health_dash,
        category_totals=category_totals, trend_labels=trend_labels,
        trend_values=trend_values, income_breakdown=income_breakdown,
        top_expenses=top_expenses, insights=insights,
    )


# ══════════════════════════════════════════════════════
#  REPORTS
# ══════════════════════════════════════════════════════

@app.route("/reports")
def reports():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    from_dt,to_dt,from_str,to_str,quick = _date_range_from_params(request.args)
    expenses      = _fetch_report_expenses(session['user_id'],from_dt,to_dt)
    total_expense = sum(e.amount for e in expenses)
    category_totals = defaultdict(float)
    for exp in expenses: category_totals[exp.category] += exp.amount
    return render_template("reports.html",
        email=session['user_email'], expenses=expenses,
        total_expense=total_expense, income=user.income,
        savings=user.income-total_expense,
        category_totals=dict(sorted(category_totals.items(),key=lambda x:x[1],reverse=True)),
        from_date=from_str, to_date=to_str, quick=quick,
        now=datetime.now().strftime("%d %b %Y, %I:%M %p"),
    )


@app.route("/download_csv")
def download_csv():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    from_dt,to_dt,from_str,to_str,_ = _date_range_from_params(request.args)
    expenses      = _fetch_report_expenses(session['user_id'],from_dt,to_dt)
    total_expense = sum(e.amount for e in expenses)
    category_totals = defaultdict(float)
    for exp in expenses: category_totals[exp.category] += exp.amount

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["FinTrack Expense Report"])
    writer.writerow(["Account", session['user_email']])
    writer.writerow(["Period", f"{from_str} to {to_str}"])
    writer.writerow(["Generated", datetime.now().strftime("%d %b %Y %H:%M")])
    writer.writerow([])
    writer.writerow(["SUMMARY"])
    writer.writerow(["Income", f"Rs {user.income}"])
    writer.writerow(["Total Spent", f"Rs {round(total_expense,2)}"])
    writer.writerow(["Savings", f"Rs {round(user.income-total_expense,2)}"])
    writer.writerow(["Transactions", len(expenses)])
    writer.writerow([])
    writer.writerow(["CATEGORY SUMMARY"])
    writer.writerow(["Category","Amount (Rs)","% of Total"])
    for cat,amt in sorted(category_totals.items(),key=lambda x:x[1],reverse=True):
        pct = round((amt/total_expense)*100,1) if total_expense else 0
        writer.writerow([cat, round(amt,2), f"{pct}%"])
    writer.writerow([])
    writer.writerow(["ALL EXPENSES"])
    writer.writerow(["#","Date","Category","Amount (Rs)"])
    for i,exp in enumerate(expenses,1):
        writer.writerow([i, exp.date.strftime("%d %b %Y"), exp.category, exp.amount])
    writer.writerow([])
    writer.writerow(["TOTAL","","",round(total_expense,2)])

    return Response(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=fintrack_{from_str}_to_{to_str}.csv"})


@app.route("/download_pdf")
def download_pdf():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    from_dt,to_dt,from_str,to_str,_ = _date_range_from_params(request.args)
    expenses      = _fetch_report_expenses(session['user_id'],from_dt,to_dt)
    total_expense = sum(e.amount for e in expenses)
    category_totals = defaultdict(float)
    for exp in expenses: category_totals[exp.category] += exp.amount
    category_totals = dict(sorted(category_totals.items(),key=lambda x:x[1],reverse=True))

    buffer  = io.BytesIO()
    doc     = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20*mm,rightMargin=20*mm,topMargin=20*mm,bottomMargin=20*mm)
    styles  = getSampleStyleSheet()
    GREEN   = colors.HexColor("#22C55E")
    DARK    = colors.HexColor("#111827")
    GRAY    = colors.HexColor("#94A3B8")
    LIGHTBG = colors.HexColor("#F9FAFB")

    t_style = ParagraphStyle("t",parent=styles["Normal"],fontSize=22,fontName="Helvetica-Bold",textColor=DARK,spaceAfter=2)
    s_style = ParagraphStyle("s",parent=styles["Normal"],fontSize=10,textColor=GRAY,spaceAfter=4)
    h_style = ParagraphStyle("h",parent=styles["Normal"],fontSize=11,fontName="Helvetica-Bold",textColor=DARK,spaceBefore=14,spaceAfter=6)

    story = []
    story.append(Paragraph("FinTrack Expense Report", t_style))
    story.append(Paragraph(f"Account: {session['user_email']}", s_style))
    story.append(Paragraph(f"Period: {from_str}  to  {to_str}", s_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}", s_style))
    story.append(HRFlowable(width="100%",thickness=2,color=GREEN,spaceAfter=12))

    story.append(Paragraph("Summary", h_style))
    sum_data = [["Income",f"Rs {user.income}"],["Total Spent",f"Rs {round(total_expense,2)}"],
                ["Savings",f"Rs {round(user.income-total_expense,2)}"],["Transactions",str(len(expenses))]]
    st = Table(sum_data,colWidths=[80*mm,80*mm])
    st.setStyle(TableStyle([("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),10),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white,LIGHTBG]),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB")),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),("LEFTPADDING",(0,0),(-1,-1),10)]))
    story.append(st); story.append(Spacer(1,8))

    if category_totals:
        story.append(Paragraph("Category Summary", h_style))
        cat_data = [["Category","Amount (Rs)","% of Total"]]
        for cat,amt in category_totals.items():
            pct = round((amt/total_expense)*100,1) if total_expense else 0
            cat_data.append([cat,f"Rs {round(amt,2)}",f"{pct}%"])
        ct = Table(cat_data,colWidths=[85*mm,55*mm,40*mm])
        ct.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),9),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHTBG]),
            ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB")),
            ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
            ("LEFTPADDING",(0,0),(-1,-1),10),("ALIGN",(1,0),(-1,-1),"RIGHT")]))
        story.append(ct); story.append(Spacer(1,8))

    story.append(Paragraph("All Expenses", h_style))
    if expenses:
        exp_data = [["#","Date","Category","Amount (Rs)"]]
        for i,exp in enumerate(expenses,1):
            exp_data.append([str(i),exp.date.strftime("%d %b %Y"),exp.category,f"Rs {exp.amount}"])
        exp_data.append(["","","TOTAL",f"Rs {round(total_expense,2)}"])
        et = Table(exp_data,colWidths=[15*mm,40*mm,80*mm,45*mm])
        et.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),DARK),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),9),("ROWBACKGROUNDS",(0,1),(-1,-2),[colors.white,LIGHTBG]),
            ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#F3F4F6")),
            ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#E5E7EB")),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LEFTPADDING",(0,0),(-1,-1),8),("ALIGN",(3,0),(3,-1),"RIGHT"),("ALIGN",(0,0),(0,-1),"CENTER")]))
        story.append(et)
    else:
        story.append(Paragraph("No expenses found for this period.", s_style))

    story.append(Spacer(1,16))
    story.append(HRFlowable(width="100%",thickness=0.5,color=colors.HexColor("#E5E7EB"),spaceAfter=6))
    story.append(Paragraph(f"Generated by FinTrack · {datetime.now().strftime('%d %b %Y %H:%M')} · All amounts in INR",
        ParagraphStyle("footer",parent=styles["Normal"],fontSize=8,textColor=GRAY,alignment=TA_CENTER)))

    doc.build(story)
    pdf_bytes = buffer.getvalue(); buffer.close()
    response  = make_response(pdf_bytes)
    response.headers["Content-Type"]        = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=fintrack_{from_str}_to_{to_str}.pdf"
    return response


# ══════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════

@app.route("/settings")
def settings():
    if 'user_id' not in session: return redirect(url_for('login'))
    user    = db.session.get(User, session['user_id'])
    profile = _get_or_create_profile(session['user_id'])

    total_expenses    = Expense.query.filter_by(user_id=session['user_id']).count()
    total_income_logs = IncomeLog.query.filter_by(user_id=session['user_id']).count()
    all_expenses      = Expense.query.filter_by(user_id=session['user_id']).all()
    total_spent       = round(sum(e.amount for e in all_expenses), 2)

    return render_template("settings.html",
        email=session['user_email'], profile=profile,
        total_expenses=total_expenses, total_income_logs=total_income_logs,
        total_spent=total_spent,
    )


@app.route("/settings/profile", methods=["POST"])
def settings_profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    profile           = _get_or_create_profile(session['user_id'])
    profile.full_name = request.form.get("full_name","").strip()
    profile.phone     = request.form.get("phone","").strip()
    profile.avatar    = request.form.get("avatar","😊")
    db.session.commit()
    return redirect(url_for('settings') + '?success=Profile+saved+successfully#profile')


@app.route("/settings/preferences", methods=["POST"])
def settings_preferences():
    if 'user_id' not in session: return redirect(url_for('login'))
    profile          = _get_or_create_profile(session['user_id'])
    profile.currency = request.form.get("currency","₹")
    db.session.commit()
    return redirect(url_for('settings') + '?success=Preferences+saved#preferences')


@app.route("/settings/password", methods=["POST"])
def settings_password():
    if 'user_id' not in session: return redirect(url_for('login'))
    user        = db.session.get(User, session['user_id'])
    current_pw  = request.form.get("current_password","")
    new_pw      = request.form.get("new_password","")
    confirm_pw  = request.form.get("confirm_password","")

    if not bcrypt.check_password_hash(user.password, current_pw):
        return redirect(url_for('settings') + '?error=Current+password+is+incorrect#security')
    if new_pw != confirm_pw:
        return redirect(url_for('settings') + '?error=New+passwords+do+not+match#security')
    if len(new_pw) < 6:
        return redirect(url_for('settings') + '?error=Password+must+be+at+least+6+characters#security')

    user.password = bcrypt.generate_password_hash(new_pw).decode('utf-8')
    db.session.commit()
    return redirect(url_for('settings') + '?success=Password+updated+successfully#security')


@app.route("/delete_account", methods=["POST"])
def delete_account():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    # Delete all user data in order
    IncomeLog.query.filter_by(user_id=uid).delete()
    Expense.query.filter_by(user_id=uid).delete()
    Profile.query.filter_by(user_id=uid).delete()
    db.session.delete(db.session.get(User, uid))
    db.session.commit()
    session.clear()
    return redirect(url_for('home'))


# ══════════════════════════════════════════════════════
#  AI ASSISTANT
# ══════════════════════════════════════════════════════

@app.route("/assistant")
def assistant():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    now  = datetime.now()

    # Today
    today_start = datetime(now.year, now.month, now.day)
    today_exps  = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=today_start).all()
    today_spent = round(sum(e.amount for e in today_exps), 2)

    # This week
    week_start  = now - timedelta(days=7)
    week_exps   = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=week_start).all()
    week_spent  = round(sum(e.amount for e in week_exps), 2)

    # This month
    month_start = datetime(now.year, now.month, 1)
    month_exps  = Expense.query.filter(Expense.user_id==session['user_id'], Expense.date>=month_start).all()
    month_spent = round(sum(e.amount for e in month_exps), 2)

    # Last month
    first_this  = datetime(now.year, now.month, 1)
    last_month_end   = first_this - timedelta(seconds=1)
    last_month_start = datetime(last_month_end.year, last_month_end.month, 1)
    last_month_exps  = Expense.query.filter(
        Expense.user_id==session['user_id'],
        Expense.date>=last_month_start, Expense.date<=last_month_end
    ).all()
    last_month_spent = round(sum(e.amount for e in last_month_exps), 2)

    # Category totals this month
    cat_totals = defaultdict(float)
    for exp in month_exps: cat_totals[exp.category] += exp.amount
    top_category = max(cat_totals, key=cat_totals.get) if cat_totals else "—"
    top_cat_amt  = round(cat_totals.get(top_category, 0), 2)

    # Biggest expense ever
    biggest_exp = Expense.query.filter_by(user_id=session['user_id'])\
                                .order_by(Expense.amount.desc()).first()

    # Budget & savings
    budget_left     = round(user.budget - month_spent, 2) if user.budget > 0 else 0
    budget_used_pct = round(min((month_spent/user.budget)*100,100),1) if user.budget>0 else 0
    savings         = round(user.income - month_spent, 2)
    goal_progress   = round(max(0,min(100,(savings/user.savings_goal)*100)),1) if user.savings_goal>0 else 0
    money_remaining = round(user.income - month_spent, 2)

    # Days left in month + safe daily limit
    import calendar, json
    days_in_month      = calendar.monthrange(now.year, now.month)[1]
    days_left_in_month = max(1, days_in_month - now.day)
    if user.budget > 0 and budget_left > 0:
        daily_safe_limit = round(budget_left / days_left_in_month, 2)
    elif money_remaining > 0:
        daily_safe_limit = round(money_remaining / days_left_in_month, 2)
    else:
        daily_safe_limit = 0.0

    cat_breakdown_json = json.dumps({k: round(v, 2) for k, v in cat_totals.items()})

    # Currency from profile
    profile  = _get_or_create_profile(session['user_id'])
    currency = profile.currency or '\u20b9'

    # Lend & Borrow stats for assistant
    uid = session['user_id']
    all_lends   = LendBorrow.query.filter_by(user_id=uid, type='lend').all()
    all_borrows = LendBorrow.query.filter_by(user_id=uid, type='borrow').all()

    lb_total_lent_out  = round(sum(l.outstanding for l in all_lends  if l.status == 'active'), 2)
    lb_total_owed_back = round(sum(b.outstanding for b in all_borrows if b.status == 'active'), 2)
    lb_overdue_lends   = [l for l in all_lends   if l.is_overdue]
    lb_overdue_borrows = [b for b in all_borrows if b.is_overdue]
    lb_active_lends    = [l for l in all_lends   if l.status == 'active']
    lb_active_borrows  = [b for b in all_borrows if b.status == 'active']

    # Build lend/borrow JSON for JS
    lb_lends_json = json.dumps([{
        'name': l.person_name, 'amount': l.amount,
        'outstanding': l.outstanding, 'overdue': l.is_overdue,
        'due_date': l.due_date.strftime('%d %b %Y') if l.due_date else None,
        'reason': l.reason
    } for l in lb_active_lends])

    lb_borrows_json = json.dumps([{
        'name': b.person_name, 'amount': b.amount,
        'outstanding': b.outstanding, 'overdue': b.is_overdue,
        'due_date': b.due_date.strftime('%d %b %Y') if b.due_date else None,
        'reason': b.reason
    } for b in lb_active_borrows])

    return render_template("assistant.html",
        email=session['user_email'],
        income=user.income, budget=user.budget, savings_goal=user.savings_goal,
        today_spent=today_spent, week_spent=week_spent,
        month_spent=month_spent, last_month_spent=last_month_spent,
        this_month_spent=month_spent,
        top_category=top_category, top_cat_amt=top_cat_amt,
        biggest_exp=biggest_exp,
        budget_left=budget_left, budget_used_pct=budget_used_pct,
        savings=savings, goal_progress=goal_progress,
        money_remaining=money_remaining,
        daily_safe_limit=daily_safe_limit,
        days_left_in_month=days_left_in_month,
        cat_breakdown_json=cat_breakdown_json,
        currency=currency,
        lb_total_lent_out=lb_total_lent_out,
        lb_total_owed_back=lb_total_owed_back,
        lb_overdue_lends_count=len(lb_overdue_lends),
        lb_overdue_borrows_count=len(lb_overdue_borrows),
        lb_active_lends_count=len(lb_active_lends),
        lb_active_borrows_count=len(lb_active_borrows),
        lb_lends_json=lb_lends_json,
        lb_borrows_json=lb_borrows_json,
    )


# ══════════════════════════════════════════════════════
#  OCCASIONAL FUND ROUTES
# ══════════════════════════════════════════════════════

@app.route("/set_occasional_fund", methods=["POST"])
def set_occasional_fund():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    user.occasional_fund = float(request.form.get("amount", 0))
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route("/add_occasional_spend", methods=["POST"])
def add_occasional_spend():
    if 'user_id' not in session: return redirect(url_for('login'))
    amount   = float(request.form.get("amount", 0))
    label    = request.form.get("label", "").strip() or "Occasional"
    category = request.form.get("category", "Other").strip()

    # Only allow spending if fund has balance
    user = db.session.get(User, session['user_id'])
    occ_spent = sum(
        s.amount for s in OccasionalSpend.query.filter_by(user_id=session['user_id']).all()
    )
    if amount > (user.occasional_fund - occ_spent):
        # Still save it but mark as over — frontend handles the warning
        pass

    db.session.add(OccasionalSpend(
        amount=amount, label=label, category=category,
        user_id=session['user_id']
    ))
    db.session.commit()
    return redirect(url_for('dashboard') + '#occasional-fund')


# ══════════════════════════════════════════════════════
#  FINANCE FORM ROUTES
# ══════════════════════════════════════════════════════

@app.route("/set_income", methods=["POST"])
def set_income():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    user.income = float(request.form.get("income"))
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route("/add_income", methods=["POST"])
def add_income():
    if 'user_id' not in session: return redirect(url_for('login'))
    amount = float(request.form.get("amount",0))
    label  = request.form.get("label","Bonus").strip() or "Bonus"
    user   = db.session.get(User, session['user_id'])
    user.income += amount
    db.session.add(IncomeLog(amount=amount, label=label, user_id=session['user_id']))
    db.session.commit()
    return redirect(url_for('dashboard') + '?tab=add')


@app.route("/set_budget", methods=["POST"])
def set_budget():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    user.budget = float(request.form.get("budget"))
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route("/set_goal", methods=["POST"])
def set_goal():
    if 'user_id' not in session: return redirect(url_for('login'))
    user = db.session.get(User, session['user_id'])
    user.savings_goal = float(request.form.get("goal"))
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route("/add_expense", methods=["POST"])
def add_expense():
    if 'user_id' not in session: return redirect(url_for('login'))
    db.session.add(Expense(
        amount=float(request.form.get("amount")),
        category=request.form.get("category"),
        user_id=session['user_id']
    ))
    db.session.commit()
    return redirect(url_for('dashboard'))



# ══════════════════════════════════════════════════════
#  LEND & BORROW ROUTES
# ══════════════════════════════════════════════════════

@app.route("/lend-borrow")
def lend_borrow():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid    = session['user_id']
    tab    = request.args.get('tab', 'lend')
    now    = datetime.now()

    lends   = LendBorrow.query.filter_by(user_id=uid, type='lend').order_by(LendBorrow.date.desc()).all()
    borrows = LendBorrow.query.filter_by(user_id=uid, type='borrow').order_by(LendBorrow.date.desc()).all()

    # Summary stats
    total_lent        = sum(l.amount for l in lends)
    total_lent_out    = sum(l.outstanding for l in lends if l.status == 'active')
    total_lent_settled= sum(l.amount for l in lends if l.status == 'settled')

    total_borrowed     = sum(b.amount for b in borrows)
    total_owed_back    = sum(b.outstanding for b in borrows if b.status == 'active')
    total_borr_settled = sum(b.amount for b in borrows if b.status == 'settled')

    overdue_lends   = [l for l in lends   if l.is_overdue]
    overdue_borrows = [b for b in borrows if b.is_overdue]

    return render_template("lend_borrow.html",
        email=session['user_email'], tab=tab,
        lends=lends, borrows=borrows,
        total_lent=total_lent, total_lent_out=total_lent_out,
        total_lent_settled=total_lent_settled,
        total_borrowed=total_borrowed, total_owed_back=total_owed_back,
        total_borr_settled=total_borr_settled,
        overdue_lends=overdue_lends, overdue_borrows=overdue_borrows,
        now=now,
    )


@app.route("/lend-borrow/add", methods=["POST"])
def add_lend_borrow():
    if 'user_id' not in session: return redirect(url_for('login'))
    ltype        = request.form.get("type", "lend")
    person_name  = request.form.get("person_name", "").strip()
    phone        = request.form.get("phone", "").strip()
    address      = request.form.get("address", "").strip()
    amount       = float(request.form.get("amount", 0))
    reason       = request.form.get("reason", "").strip()
    interest_pct = float(request.form.get("interest_pct", 0) or 0)
    due_str      = request.form.get("due_date", "").strip()
    due_date     = datetime.strptime(due_str, "%Y-%m-%d") if due_str else None

    record = LendBorrow(
        type=ltype, person_name=person_name, phone=phone,
        address=address, amount=amount, reason=reason,
        interest_pct=interest_pct, due_date=due_date,
        user_id=session['user_id']
    )
    db.session.add(record)
    db.session.commit()
    return redirect(url_for('lend_borrow') + f'?tab={ltype}')


@app.route("/lend-borrow/repay/<int:loan_id>", methods=["POST"])
def add_repayment(loan_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    loan   = db.session.get(LendBorrow, loan_id)
    if not loan or loan.user_id != session['user_id']:
        return redirect(url_for('lend_borrow'))
    amount = float(request.form.get("amount", 0))
    note   = request.form.get("note", "").strip()
    db.session.add(Repayment(loan_id=loan_id, amount=amount, note=note))
    # Auto-settle if fully paid
    total_after = sum(r.amount for r in loan.repayments) + amount
    if total_after >= loan.amount:
        loan.status = 'settled'
    db.session.commit()
    return redirect(url_for('lend_borrow') + f'?tab={loan.type}')


@app.route("/lend-borrow/settle/<int:loan_id>", methods=["POST"])
def settle_loan(loan_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    loan = db.session.get(LendBorrow, loan_id)
    if loan and loan.user_id == session['user_id']:
        loan.status = 'settled'
        db.session.commit()
    return redirect(url_for('lend_borrow') + f'?tab={loan.type}')


@app.route("/lend-borrow/delete/<int:loan_id>", methods=["POST"])
def delete_loan(loan_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    loan = db.session.get(LendBorrow, loan_id)
    if loan and loan.user_id == session['user_id']:
        ltype = loan.type
        db.session.delete(loan)
        db.session.commit()
        return redirect(url_for('lend_borrow') + f'?tab={ltype}')
    return redirect(url_for('lend_borrow'))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)