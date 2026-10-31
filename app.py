import os
import secrets
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, request, jsonify, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import select
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()
DEPARTMENTS = ['Facilities', 'Hostel', 'IT Services', 'Library', 'Transport', 'Sanitation', 'Canteen', 'Laboratories']
CATEGORIES = dict(zip(['Classroom', 'Hostel', 'Internet', 'Library', 'Transportation', 'Cleanliness', 'Canteen', 'Laboratory', 'Drinking Water', 'Washroom'], ['Facilities','Hostel','IT Services','Library','Transport','Sanitation','Canteen','Laboratories','Facilities','Sanitation']))
SLA = {'Low': 168, 'Medium': 72, 'High': 24, 'Critical': 8}
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def iso(value): return value.isoformat() + 'Z' if value else None

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='student')
    department = db.Column(db.String(80))
    is_demo = db.Column(db.Boolean, default=False, nullable=False)

class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    initial_department = db.Column(db.String(80), nullable=False)
    location = db.Column(db.String(120), nullable=False)
    severity = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Submitted')
    submitted_at = db.Column(db.DateTime, nullable=False, default=now)
    resolved_at = db.Column(db.DateTime)
    sla_hours = db.Column(db.Integer, nullable=False)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    events = db.relationship('Event', backref='complaint', cascade='all, delete-orphan', order_by='Event.created_at')

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    complaint_id = db.Column(db.Integer, db.ForeignKey('complaint.id'), nullable=False)
    actor = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

class ModelVersion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    report = db.Column(db.JSON, nullable=False)

class ComplaintBrief(db.Model):
    """Additive table: preserves existing complaints without changing their schema."""
    complaint_id = db.Column(db.Integer, db.ForeignKey('complaint.id'), primary_key=True)
    reported_impact = db.Column(db.Text, nullable=False, default='')
    reported_cause = db.Column(db.Text, nullable=False, default='')
    reported_reason = db.Column(db.Text, nullable=False, default='')
    checker_cause = db.Column(db.Text)
    checker_effect = db.Column(db.Text)
    checker_reason = db.Column(db.Text)
    reviewed_by = db.Column(db.String(100))
    reviewed_at = db.Column(db.DateTime)

def create_app(test_config=None):
    app = Flask(__name__, static_folder='static')
    url = os.getenv('DATABASE_URL', 'sqlite:///campus.db')
    if url.startswith('postgres://'): url = url.replace('postgres://', 'postgresql+psycopg://', 1)
    elif url.startswith('postgresql://'): url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    production = os.getenv('APP_ENV') == 'production'
    if production and not os.getenv('DATABASE_URL'):
        raise RuntimeError('DATABASE_URL must point to persistent PostgreSQL in production')
    secret = os.getenv('SECRET_KEY')
    if production and not secret: raise RuntimeError('SECRET_KEY must be set in production')
    app.config.update(SECRET_KEY=secret or secrets.token_hex(32), SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False, MAX_CONTENT_LENGTH=64*1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    if test_config: app.config.update(test_config)
    db.init_app(app)
    # One production worker: shared per-process limiter, no untrusted forwarded IP headers.
    limiter = Limiter(get_remote_address, app=app, default_limits=['300 per minute'], storage_uri='memory://')
    app.extensions['campus_limiter'] = limiter

    def portal(): return request.headers.get('X-Portal', '')
    def current():
        uid=session.get('uid_'+portal()) if portal() in ['registrar','checker'] else None
        u=db.session.get(User,uid) if uid else None
        if u and ((portal()=='registrar' and u.role=='student') or (portal()=='checker' and u.role in ['officer','admin'])): return u
        return None
    def sign_in(u):
        # Independent identities and CSRF tokens let both portals stay signed in.
        session['uid_'+portal()]=u.id
        session['csrf_'+portal()]=secrets.token_hex(32)
        session.permanent=True
        return jsonify(user=user_json(u),csrf=session['csrf_'+portal()])
    def auth(*roles):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                u = current()
                if not u: return jsonify(error='Please sign in.'), 401
                if roles and u.role not in roles: return jsonify(error='You do not have permission.'), 403
                return fn(*args, **kwargs)
            return wrapped
        return decorate
    def visible(c, u):
        # Demo accounts operate only on sample records. Real students never expose complaints to demo staff.
        if u.is_demo != c.is_demo: return False
        return u.role == 'admin' or (u.role == 'officer' and c.department == u.department) or (u.role == 'student' and c.user_id == u.id)
    def user_json(u): return dict(id=u.id, name=u.name, email=u.email, role=u.role, department=u.department, is_demo=u.is_demo)
    def serialize(c):
        from triage import build_brief
        hours = ((c.resolved_at or now()) - c.submitted_at).total_seconds()/3600
        return dict(id=c.id, title=c.title, description=c.description, category=c.category, department=c.department,
            location=c.location, severity=c.severity, status=c.status, submitted_at=iso(c.submitted_at),
            resolved_at=iso(c.resolved_at), sla_hours=c.sla_hours, due_at=iso(c.submitted_at+timedelta(hours=c.sla_hours)),
            elapsed_hours=round(hours,1), overdue=hours>c.sla_hours, is_demo=c.is_demo,
            brief=build_brief(c,db.session.get(ComplaintBrief,c.id)),
            events=[dict(actor=e.actor,message=e.message,created_at=iso(e.created_at)) for e in c.events])

    @app.before_request
    def csrf():
        if request.path.startswith('/api/') and request.method in ['POST','PATCH','PUT','DELETE']:
            if portal() not in ['registrar','checker']: return jsonify(error='Choose the complaint registration or checker portal.'),400
            token = request.headers.get('X-CSRF-Token', '')
            if not token or not secrets.compare_digest(token, session.get('csrf_'+portal(),'')):
                return jsonify(error='Session expired. Refresh the page and retry.'), 403

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if production: response.headers['Strict-Transport-Security']='max-age=31536000'
        if request.path.startswith('/api/'): response.headers['Cache-Control']='no-store'
        return response

    @app.errorhandler(413)
    def too_large(e): return jsonify(error='Request is too large.'), 413
    @app.errorhandler(429)
    def limited(e): return jsonify(error='Too many requests. Please try again shortly.'), 429
    @app.errorhandler(400)
    def bad(e): return jsonify(error='Invalid request.'), 400

    @app.get('/')
    @app.get('/register-complaint')
    @app.get('/complaint-checker')
    def index(): return send_from_directory(app.static_folder, 'index.html')
    @app.get('/health')
    def health():
        db.session.execute(select(1))
        return jsonify(status='ok')
    @app.get('/api/session')
    def get_session():
        if portal() not in ['registrar','checker']: return jsonify(error='Choose a portal.'),400
        key='csrf_'+portal()
        if key not in session: session[key]=secrets.token_hex(32)
        u=current()
        return jsonify(user=user_json(u) if u else None, csrf=session[key], categories=list(CATEGORIES), departments=DEPARTMENTS, sla=SLA)

    @app.post('/api/register')
    @limiter.limit('5 per hour')
    def register():
        if portal()!='registrar': return jsonify(error='Student accounts are created in the complaint registration portal. Checker access is assigned by an administrator.'),403
        d=request.get_json() or {}; name=str(d.get('name','')).strip(); email=str(d.get('email','')).strip().lower(); password=str(d.get('password',''))
        if not 2<=len(name)<=100 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254 or not 12<=len(password)<=128:
            return jsonify(error='Enter a name, valid email, and a password of 12–128 characters.'),400
        if db.session.scalar(select(User).where(User.email==email)): return jsonify(error='Unable to register this email. Try signing in.'),400
        u=User(name=name,email=email,password=generate_password_hash(password),role='student')
        db.session.add(u); db.session.commit()
        response=sign_in(u); response.status_code=201; return response

    @app.post('/api/auth/<login_portal>/login')
    @limiter.limit('10 per minute')
    def login(login_portal):
        if login_portal!=portal(): return jsonify(error='Login portal does not match.'),400
        d=request.get_json() or {}; email=str(d.get('email','')).strip().lower(); password=str(d.get('password',''))[:129]
        u=db.session.scalar(select(User).where(User.email==email))
        hashed=u.password if u else app.config['DUMMY_HASH']
        if not check_password_hash(hashed,password) or not u: return jsonify(error='Email or password is incorrect.'),401
        if (portal()=='registrar' and u.role!='student') or (portal()=='checker' and u.role not in ['officer','admin']):
            return jsonify(error='This account belongs to the other portal. Use a student account for registration or a staff account for the checker.'),403
        return sign_in(u)

    @app.post('/api/auth/<login_portal>/demo')
    @limiter.limit('20 per minute')
    def demo(login_portal):
        if login_portal!=portal(): return jsonify(error='Login portal does not match.'),400
        role=(request.get_json() or {}).get('role')
        if role not in ['student','officer','admin']: return jsonify(error='Invalid role.'),400
        if (portal()=='registrar' and role!='student') or (portal()=='checker' and role=='student'): return jsonify(error='Choose a demo account for this portal.'),403
        u=db.session.scalar(select(User).where(User.email==f'{role}@demo.campus',User.is_demo.is_(True)))
        if not u: return jsonify(error='Demo is disabled.'),404
        return sign_in(u)

    @app.post('/api/logout')
    def logout():
        session.pop('uid_'+portal(),None); session.pop('csrf_'+portal(),None)
        return jsonify(ok=True)

    @app.get('/api/complaints')
    @auth()
    def complaints():
        u=current(); q=select(Complaint).where(Complaint.is_demo==u.is_demo)
        if u.role=='student': q=q.where(Complaint.user_id==u.id)
        if u.role=='officer': q=q.where(Complaint.department==u.department)
        return jsonify(complaints=[serialize(c) for c in db.session.scalars(q.order_by(Complaint.submitted_at.desc()))])

    @app.post('/api/complaints')
    @auth('student')
    @limiter.limit('15 per hour')
    def submit():
        d=request.get_json() or {}; u=current()
        values={k:str(d.get(k,'')).strip() for k in ['title','description','category','location','severity']}
        if not 5<=len(values['title'])<=160 or not 15<=len(values['description'])<=5000 or not 2<=len(values['location'])<=120 or values['category'] not in CATEGORIES or values['severity'] not in SLA:
            return jsonify(error='Check all fields: title 5–160, description 15–5000, location 2–120 characters.'),400
        dep=CATEGORIES[values['category']]
        impact=str(d.get('reported_impact','')).strip(); reason=str(d.get('reported_reason','')).strip(); cause=str(d.get('reported_cause','')).strip()
        if not 5<=len(impact)<=2000 or not 5<=len(reason)<=2000 or len(cause)>2000:
            return jsonify(error='Explain who or what is affected and why attention is needed (5–2000 characters each). Cause is optional, up to 2000 characters.'),400
        c=Complaint(**values,user_id=u.id,department=dep,initial_department=dep,sla_hours=SLA[values['severity']],is_demo=u.is_demo)
        db.session.add(c); db.session.flush()
        db.session.add(ComplaintBrief(complaint_id=c.id,reported_impact=impact,reported_cause=cause,reported_reason=reason))
        db.session.add(Event(complaint_id=c.id,actor=u.name,message=f'Complaint conveyed to {dep} for checking. No repair or resolution is confirmed.'))
        db.session.commit(); return jsonify(complaint=serialize(c)),201

    @app.patch('/api/complaints/<int:cid>')
    @auth('officer','admin')
    def update(cid):
        c=db.session.get(Complaint,cid); u=current()
        if not c or not visible(c,u): return jsonify(error='Complaint not found.'),404
        d=request.get_json() or {}; message=str(d.get('message','')).strip(); status=d.get('status',c.status); dep=d.get('department',c.department)
        if not 5<=len(message)<=2000 or status not in ['Submitted','Reviewed','Forwarded'] or dep not in DEPARTMENTS:
            return jsonify(error='Select a valid status and add an update of 5–2000 characters.'),400
        if dep!=c.department and u.role!='admin': return jsonify(error='Only administrators can reassign departments.'),403
        if c.resolved_at: return jsonify(error='This is a historical resolution record. New complaints can only be reviewed or forwarded here.'),400
        if c.department!=dep: message=f'Reassigned from {c.department} to {dep}. '+message
        c.department=dep
        c.status=status; db.session.add(Event(complaint_id=c.id,actor=u.name,message=f'{status}: {message}')); db.session.commit()
        return jsonify(complaint=serialize(c))

    @app.patch('/api/complaints/<int:cid>/brief')
    @auth('officer','admin')
    def review_brief(cid):
        c=db.session.get(Complaint,cid); u=current()
        if not c or not visible(c,u): return jsonify(error='Complaint not found.'),404
        d=request.get_json() or {}
        values={k:str(d.get(k,'')).strip() for k in ['checker_cause','checker_effect','checker_reason']}
        if any(not 5<=len(v)<=2000 for v in values.values()): return jsonify(error='Provide cause assessment, effects and reason for attention (5–2000 characters each). Use “Not established” when evidence is missing.'),400
        b=db.session.get(ComplaintBrief,cid)
        if not b: b=ComplaintBrief(complaint_id=cid); db.session.add(b)
        for k,v in values.items(): setattr(b,k,v)
        b.reviewed_by=u.name; b.reviewed_at=now()
        if not c.resolved_at: c.status='Reviewed'
        db.session.add(Event(complaint_id=cid,actor=u.name,message='Checker assessment recorded. Cause remains an assessment; no repair or physical resolution is confirmed.'))
        db.session.commit(); return jsonify(complaint=serialize(c))

    @app.get('/api/analytics')
    @auth('admin')
    def analytics():
        u=current(); rows=list(db.session.scalars(select(Complaint).where(Complaint.is_demo==u.is_demo)))
        def stats(items):
            closed=[c for c in items if c.resolved_at]; delayed=[c for c in closed if (c.resolved_at-c.submitted_at).total_seconds()>c.sla_hours*3600]
            return dict(total=len(items),open=sum(c.status!='Resolved' for c in items),resolved=len(closed),delayed=len(delayed),
                overdue=sum(c.status!='Resolved' and now()>c.submitted_at+timedelta(hours=c.sla_hours) for c in items),
                mean_hours=round(sum((c.resolved_at-c.submitted_at).total_seconds()/3600 for c in closed)/len(closed),1) if closed else None,
                delay_rate=round(len(delayed)/len(closed)*100,1) if closed else None)
        trends=[]
        for days in range(6,-1,-1):
            date=(now()-timedelta(days=days)).date()
            trends.append(dict(date=str(date),submitted=sum(c.submitted_at.date()==date for c in rows),resolved=sum(bool(c.resolved_at and c.resolved_at.date()==date) for c in rows)))
        return jsonify(summary=stats(rows),departments=[dict(name=d,**stats([c for c in rows if c.department==d])) for d in DEPARTMENTS],
            categories=[dict(name=k,**stats([c for c in rows if c.category==k])) for k in CATEGORIES],trends=trends,is_demo=u.is_demo)

    @app.get('/api/model')
    @auth()
    def model_status():
        from ml import get_report
        return jsonify(get_report())

    @app.post('/api/predict')
    @auth()
    def predict():
        from ml import predict_delay
        d=request.get_json() or {}
        if d.get('category') not in CATEGORIES or d.get('severity') not in SLA or not 2<=len(str(d.get('location','')))<=120 or not 15<=len(str(d.get('description','')))<=5000:
            return jsonify(error='Complete the complaint details before checking risk.'),400
        return jsonify(predict_delay(d,CATEGORIES[d['category']],SLA[d['severity']]))

    @app.post('/api/model/train')
    @auth('admin')
    @limiter.limit('2 per hour')
    def train():
        if current().is_demo: return jsonify(error='Demo accounts cannot train on real campus data.'),403
        from ml import train_model
        return jsonify(train_model())

    @app.get('/api/users')
    @auth('admin')
    def users():
        return jsonify(users=[user_json(u) for u in db.session.scalars(select(User).where(User.is_demo==current().is_demo).order_by(User.name))])

    @app.patch('/api/users/<int:uid>')
    @auth('admin')
    def set_role(uid):
        me=current(); u=db.session.get(User,uid); d=request.get_json() or {}; role=d.get('role'); dep=d.get('department')
        if not u or u.is_demo!=me.is_demo: return jsonify(error='User not found.'),404
        if me.is_demo: return jsonify(error='Demo staff permissions are fixed.'),403
        if u.id==me.id: return jsonify(error='You cannot change your own role.'),400
        if role not in ['student','officer','admin'] or (role=='officer' and dep not in DEPARTMENTS): return jsonify(error='Choose a valid role and department.'),400
        u.role=role; u.department=dep if role=='officer' else None; db.session.commit(); return jsonify(user=user_json(u))

    with app.app_context():
        db.create_all(); app.config['DUMMY_HASH']=generate_password_hash(secrets.token_hex(20))
        if not app.config.get('TESTING'): initialize()
    return app

def initialize():
    from seed import seed_demo
    if os.getenv('SEED_DEMO','true').lower()=='true': seed_demo()
    email=os.getenv('ADMIN_EMAIL'); password=os.getenv('ADMIN_PASSWORD')
    if email and password:
        if len(password)<12: raise RuntimeError('ADMIN_PASSWORD must have at least 12 characters')
        if not db.session.scalar(select(User).where(User.email==email.lower())):
            db.session.add(User(name='College Administrator',email=email.lower(),password=generate_password_hash(password),role='admin',is_demo=False)); db.session.commit()

if __name__=='__main__':
    # Keep a single module identity when invoked directly; seed and ML import app.
    import sys
    sys.modules['app']=sys.modules[__name__]
    create_app().run(host='0.0.0.0',port=int(os.getenv('PORT',5000)))
