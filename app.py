import os
import smtplib
import threading
import time
import io
import re
import json
import pandas as pd
from urllib.parse import quote, unquote
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# ================= APP CONFIG =================
app = Flask(__name__)
app.secret_key = "marketing_pro_2025_secure_key"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///marketing_crm_2025.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# নতুন ক্লাউডফ্লেয়ার লিঙ্ক
BASE_URL = "https://advertisers-gel-powers-targeted.trycloudflare.com".rstrip('/')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ================= MODELS =================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50))
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    campaigns = db.relationship('Campaign', backref='owner', lazy=True)
    leads = db.relationship('Lead', backref='owner', lazy=True)
    accounts = db.relationship('ConnectedAccount', backref='owner', lazy=True)
    settings = db.relationship('SystemSettings', backref='owner', uselist=False, lazy=True)

class SystemSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    global_daily_limit = db.Column(db.Integer, default=500)
    global_delay = db.Column(db.Integer, default=30)
    tracking_domain = db.Column(db.String(200))
    smtp_host = db.Column(db.String(100))
    smtp_port = db.Column(db.String(10))
    smtp_user = db.Column(db.String(100))
    smtp_pass = db.Column(db.String(100))

class ConnectedAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    provider = db.Column(db.String(50))
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False) 
    daily_limit = db.Column(db.Integer, default=500)
    smtp_host = db.Column(db.String(100), default='smtp.gmail.com')
    smtp_port = db.Column(db.Integer, default=587)

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    list_name = db.Column(db.String(100))
    first_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    email = db.Column(db.String(120))
    company = db.Column(db.String(100))
    custom_field = db.Column(db.Text) 
    opened = db.Column(db.Boolean, default=False)
    open_count = db.Column(db.Integer, default=0)
    clicked = db.Column(db.Boolean, default=False)
    click_count = db.Column(db.Integer, default=0)

class Campaign(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    name = db.Column(db.String(100))
    subject = db.Column(db.String(200))
    body = db.Column(db.Text)
    signature = db.Column(db.Text)
    lead_list = db.Column(db.String(100))
    status = db.Column(db.String(20), default='Paused') 
    sent_count = db.Column(db.Integer, default=0)
    total_leads = db.Column(db.Integer, default=0)
    open_total = db.Column(db.Integer, default=0)
    click_total = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ================= EMAIL WORKER & TRACKING =================

def send_campaign_worker(app_instance, campaign_id, user_id):
    with app_instance.app_context():
        campaign = Campaign.query.get(campaign_id)
        user_account = ConnectedAccount.query.filter_by(user_id=user_id).first()
        leads = Lead.query.filter_by(list_name=campaign.lead_list, user_id=user_id).all()
        current_owner = User.query.get(user_id)
        user_settings = SystemSettings.query.filter_by(user_id=user_id).first()
        delay_time = user_settings.global_delay if user_settings else 30

        if not user_account or not leads:
            campaign.status = 'Error: No Setup'
            db.session.commit()
            return

        try:
            server = smtplib.SMTP(user_account.smtp_host, user_account.smtp_port)
            server.starttls()
            server.login(user_account.email, user_account.password)

            for lead in leads:
                db.session.refresh(campaign)
                if campaign.status != 'Running': break

                def apply_personalization(text, lead_data):
                    text = text.replace('{first_name}', lead_data.first_name or "")
                    text = text.replace('{last_name}', lead_data.last_name or "")
                    text = text.replace('{company}', lead_data.company or "")
                    if lead_data.custom_field:
                        try:
                            extra = json.loads(lead_data.custom_field)
                            for key, val in extra.items():
                                text = text.replace(f'{{{key}}}', str(val))
                        except: pass
                    return text

                msg = MIMEMultipart()
                msg['From'] = f"{current_owner.first_name} <{user_account.email}>"
                msg['To'] = lead.email
                msg['Subject'] = apply_personalization(campaign.subject, lead)
                p_body = apply_personalization(campaign.body, lead)
                
                def replace_link(match):
                    original_url = match.group(1)
                    if BASE_URL in original_url: return match.group(0)
                    encoded_url = quote(original_url, safe='')
                    return f'href="{BASE_URL}/t/c/{lead.id}?url={encoded_url}&c_id={campaign.id}"'
                
                tracked_html = re.sub(r'href=["\'](https?://.*?)["\']', replace_link, p_body)
                tracking_pixel = f'<img src="{BASE_URL}/t/o/{lead.id}?c_id={campaign.id}" width="1" height="1" style="display:block !important; border:0; outline:none; opacity:0;">'
                
                final_html = f"""
                <html>
                    <body style="font-family: Arial, sans-serif; margin:0; padding:20px;">
                        <div>{tracked_html}</div>
                        <br><br>
                        <div style='color:#777; font-size:12px; border-top:1px solid #eee; padding-top:10px;'>{campaign.signature}</div>
                        {tracking_pixel}
                    </body>
                </html>
                """

                msg.attach(MIMEText(final_html, 'html'))
                server.send_message(msg)
                
                campaign.sent_count += 1
                db.session.commit()
                time.sleep(delay_time)

            campaign.status = 'Completed'
            db.session.commit()
            server.quit()
        except Exception as e:
            campaign.status = f'Failed: {str(e)}'
            db.session.commit()

# ================= TRACKING ROUTES =================

@app.route('/t/o/<int:lead_id>')
def track_open(lead_id):
    lead = Lead.query.get(lead_id)
    c_id = request.args.get('c_id')
    if lead:
        lead.opened = True
        lead.open_count += 1
        if c_id:
            campaign = Campaign.query.get(c_id)
            if campaign:
                campaign.open_total += 1
        db.session.commit()
    
    pixel = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    response = make_response(send_file(io.BytesIO(pixel), mimetype='image/gif'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/t/c/<int:lead_id>')
def track_click(lead_id):
    target_url = request.args.get('url')
    c_id = request.args.get('c_id')
    lead = Lead.query.get(lead_id)
    if lead:
        lead.clicked = True
        lead.click_count += 1
        if c_id:
            campaign = Campaign.query.get(c_id)
            if campaign:
                campaign.click_total += 1
        db.session.commit()
    return redirect(unquote(target_url))

# ================= NEW ACCOUNT REPORT ROUTE =================

@app.route('/account_report/<int:account_id>')
@login_required
def account_report(account_id):
    acc = ConnectedAccount.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    campaigns = Campaign.query.filter_by(user_id=current_user.id).all()
    
    # আপনার রিকোয়েস্ট অনুযায়ী ড্যাশবোর্ড ডাটা ক্যালকুলেশন
    stats = {
        'total_sent': sum(c.sent_count for c in campaigns),
        'total_open': sum(c.open_total for c in campaigns),
        'total_click': sum(c.click_total for c in campaigns),
        'running_campaigns': Campaign.query.filter_by(user_id=current_user.id, status='Running').count(),
        'total_campaigns': len(campaigns),
        'daily_limit': acc.daily_limit,
        'email_status': 'Active',
        'total_bounce': 0, # SMTP তে এটি সরাসরি ট্র্যাক করা সম্ভব নয় IMAP ছাড়া
        'total_block': 0,  # এটি ইমেইল ফেইলর লগ থেকে কাউন্ট করতে হয়
        'total_reply': 0   # এটি ইনবক্স রিড (IMAP) লজিক ছাড়া সম্ভব নয়
    }
    
    # ইমেইল হেলথ লজিক (ওপেন রেট বেশি হলে হেলথ ভালো)
    if stats['total_sent'] > 0:
        health_percent = (stats['total_open'] / stats['total_sent']) * 100
        stats['email_health'] = f"{round(health_percent, 1)}%"
    else:
        stats['email_health'] = "100% (Neutral)"

    return render_template('account_report.html', acc=acc, stats=stats)

# ================= ROUTES =================

@app.route('/upload_leads', methods=['POST'])
@login_required
def upload_leads():
    file = request.files.get('lead_file')
    list_name = request.form.get('list_name', 'Imported List')
    if file:
        try:
            df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
            df.columns = [c.lower().strip() for c in df.columns]
            base_cols = ['email', 'first_name', 'last_name', 'company']
            count = 0
            for _, row in df.iterrows():
                email_val = str(row.get('email', '')).strip()
                if not email_val or email_val == 'nan': continue
                custom_data = {col: str(row.get(col, '')).replace('nan', '') for col in df.columns if col not in base_cols}
                new_lead = Lead(
                    user_id=current_user.id, email=email_val,
                    first_name=str(row.get('first_name', '')).replace('nan', ''),
                    last_name=str(row.get('last_name', '')).replace('nan', ''),
                    company=str(row.get('company', '')).replace('nan', ''),
                    custom_field=json.dumps(custom_data),
                    list_name=list_name
                )
                db.session.add(new_lead)
                count += 1
            db.session.commit()
            flash(f"Imported {count} leads!", "success")
        except Exception as e: flash(str(e), "danger")
    return redirect(url_for('leads_page'))

@app.route('/')
@login_required
def index():
    accounts = ConnectedAccount.query.filter_by(user_id=current_user.id).all()
    total_leads = Lead.query.filter_by(user_id=current_user.id).count()
    total_opens = db.session.query(db.func.sum(Campaign.open_total)).filter(Campaign.user_id == current_user.id).scalar() or 0
    total_clicks = db.session.query(db.func.sum(Campaign.click_total)).filter(Campaign.user_id == current_user.id).scalar() or 0
    return render_template('index.html', accounts=accounts, user=current_user, total_leads=total_leads, total_opens=total_opens, total_clicks=total_clicks)

@app.route('/leads')
@login_required
def leads_page():
    leads = Lead.query.filter_by(user_id=current_user.id).all()
    return render_template('leads.html', leads=leads)

@app.route('/campaigns')
@login_required
def campaigns_page():
    campaigns = Campaign.query.filter_by(user_id=current_user.id).order_by(Campaign.id.desc()).all()
    lists = db.session.query(Lead.list_name).filter_by(user_id=current_user.id).distinct().all()
    return render_template('campaigns.html', campaigns=campaigns, lists=[l[0] for l in lists if l[0]])

@app.route('/create_campaign', methods=['POST'])
@login_required
def create_campaign():
    lead_list = request.form.get('lead_list')
    total_leads = Lead.query.filter_by(user_id=current_user.id, list_name=lead_list).count()
    new_campaign = Campaign(
        user_id=current_user.id, name=request.form.get('name'),
        subject=request.form.get('subject'), body=request.form.get('body'),
        signature=request.form.get('signature'), lead_list=lead_list,
        total_leads=total_leads, status='Paused'
    )
    db.session.add(new_campaign)
    db.session.commit()
    flash("Campaign Created!", "success")
    return redirect(url_for('campaigns_page'))

@app.route('/launch_campaign/<int:id>')
@login_required
def launch_campaign(id):
    campaign = Campaign.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    if campaign.status != 'Running':
        campaign.status = 'Running'
        db.session.commit()
        threading.Thread(target=send_campaign_worker, args=(app, id, current_user.id)).start()
        flash("Campaign Launched!", "success")
    return redirect(url_for('campaigns_page'))

@app.route('/delete_campaign/<int:id>')
@login_required
def delete_campaign(id):
    campaign = Campaign.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    db.session.delete(campaign)
    db.session.commit()
    flash("Campaign Deleted!", "info")
    return redirect(url_for('campaigns_page'))

@app.route('/settings')
@login_required
def settings():
    user_settings = SystemSettings.query.filter_by(user_id=current_user.id).first()
    if not user_settings:
        user_settings = SystemSettings(user_id=current_user.id)
        db.session.add(user_settings)
        db.session.commit()
    return render_template('settings.html', settings=user_settings)

@app.route('/update_settings', methods=['POST'])
@login_required
def update_settings():
    s = SystemSettings.query.filter_by(user_id=current_user.id).first()
    s.global_delay = int(request.form.get('global_delay', 30))
    db.session.commit()
    flash("Settings updated!", "success")
    return redirect(url_for('settings'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form.get('password'))
        new_user = User(first_name=request.form.get('first_name'), email=request.form.get('email'), password=hashed_pw)
        try:
            db.session.add(new_user)
            db.session.commit()
            return redirect(url_for('login'))
        except: flash("Email exists!", "danger")
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash("Invalid login!", "danger")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/connect_account', methods=['POST'])
@login_required
def connect_account():
    email = request.form.get('email')
    password = request.form.get('password')
    provider = request.form.get('provider')
    smtp_host = request.form.get('smtp_host')
    smtp_port = int(request.form.get('smtp_port'))
    try:
        new_acc = ConnectedAccount(
            user_id=current_user.id, email=email, password=password,
            provider=provider, smtp_host=smtp_host, smtp_port=smtp_port
        )
        db.session.add(new_acc)
        db.session.commit()
        flash("Account Connected!", "success")
    except Exception as e: flash(str(e), "danger")
    return redirect(url_for('index'))

@app.route('/delete_account/<int:id>')
@login_required
def delete_account(id):
    acc = ConnectedAccount.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    db.session.delete(acc)
    db.session.commit()
    flash("Email account removed!", "info")
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)