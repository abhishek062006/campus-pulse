import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from datetime import timedelta
from werkzeug.security import generate_password_hash
from app import create_app, db, User, Complaint, now
import ml

@pytest.fixture()
def app():
    ml._model=None; ml._report=None
    a=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':'sqlite://','SECRET_KEY':'test-only-secret','RATELIMIT_ENABLED':False})
    with a.app_context():
        for name,role,dep in [('s','student',None),('s2','student',None),('off','officer','Facilities'),('it','officer','IT Services'),('admin','admin',None)]:
            db.session.add(User(name=name,email=name+'@college.test',password=generate_password_hash('long-test-password'),role=role,department=dep))
        db.session.commit()
    yield a
    with a.app_context(): db.drop_all()

def login(c,name):
    token=c.get('/api/session').json['csrf']
    r=c.post('/api/login',json={'email':name+'@college.test','password':'long-test-password'},headers={'X-CSRF-Token':token})
    assert r.status_code==200
    return {'X-CSRF-Token':r.json['csrf']}

PAYLOAD=dict(title='Projector is broken',description='The projector does not start during the morning lecture.',category='Classroom',location='Block A',severity='High')

def test_production_requires_database(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with pytest.raises(RuntimeError,match='DATABASE_URL'):
        create_app()

def test_auth_csrf_and_no_self_promotion(app):
    c=app.test_client()
    assert c.get('/api/complaints').status_code==401
    assert c.post('/api/register',json={}).status_code==403
    token=c.get('/api/session').json['csrf']
    r=c.post('/api/register',headers={'X-CSRF-Token':token},json=dict(name='New Student',email='new@college.test',password='secure-password-123',role='admin',is_demo=True))
    assert r.status_code==201 and r.json['user']['role']=='student' and not r.json['user']['is_demo']
    assert c.get('/api/analytics').status_code==403

def test_full_lifecycle_permissions_assignment_and_sla(app):
    s=app.test_client(); sh=login(s,'s'); r=s.post('/api/complaints',json=PAYLOAD,headers=sh)
    assert r.status_code==201; cid=r.json['complaint']['id']
    assert r.json['complaint']['department']=='Facilities' and r.json['complaint']['sla_hours']==24
    assert s.patch(f'/api/complaints/{cid}',json={'status':'Resolved','message':'Fixed now'},headers=sh).status_code==403
    other=app.test_client(); oh=login(other,'s2'); assert other.get('/api/complaints').json['complaints']==[]
    it=app.test_client(); ih=login(it,'it'); assert it.patch(f'/api/complaints/{cid}',json={'message':'hello'},headers=ih).status_code==404
    off=app.test_client(); fh=login(off,'off')
    assert off.patch(f'/api/complaints/{cid}',json={'message':'Reassign requested','department':'IT Services'},headers=fh).status_code==403
    r=off.patch(f'/api/complaints/{cid}',json={'message':'Technician scheduled','status':'In Progress'},headers=fh); assert r.status_code==200
    admin=app.test_client(); ah=login(admin,'admin')
    assert admin.patch(f'/api/complaints/{cid}',json={'message':'IT team to inspect cable','department':'IT Services'},headers=ah).status_code==200
    assert off.get('/api/complaints').json['complaints']==[]
    r=it.patch(f'/api/complaints/{cid}',json={'status':'Resolved','message':'Cable replaced and tested'},headers=ih)
    assert r.status_code==200 and r.json['complaint']['resolved_at']
    assert len(s.get('/api/complaints').json['complaints'][0]['events'])==4
    assert it.patch(f'/api/complaints/{cid}',json={'status':'Submitted','message':'reopen test'},headers=ih).status_code==400
    a=admin.get('/api/analytics');assert a.status_code==200 and a.json['summary']['resolved']==1
    with app.app_context(): assert db.session.get(Complaint,cid).initial_department=='Facilities'

def test_demo_isolation_and_model_fallback(app):
    with app.app_context():
        from seed import seed_demo
        seed_demo()
    c=app.test_client(); h=login(c,'s'); c.post('/api/complaints',json=PAYLOAD,headers=h)
    d=app.test_client();token=d.get('/api/session').json['csrf'];r=d.post('/api/demo',json={'role':'admin'},headers={'X-CSRF-Token':token});dh={'X-CSRF-Token':r.json['csrf']}
    rows=d.get('/api/complaints').json['complaints'];assert len(rows)==96 and all(c['is_demo'] for c in rows)
    assert d.get('/api/analytics').status_code==200
    assert d.get('/api/model').json['eligible_real_rows']==0
    assert d.post('/api/model/train',json={},headers=dh).status_code==403
    p=c.post('/api/predict',json=PAYLOAD,headers=h).json;assert p['mode']=='fallback' and p['probability'] is None and p['classification']=='Not assessed'

def test_real_ml_path_temporal_holdout_and_leakage(app):
    with app.app_context():
        uid=db.session.query(User).filter_by(role='student').first().id
        base=now()-timedelta(days=220)
        for i in range(160):
            submitted=base+timedelta(days=i); delayed=i%2==0
            c=Complaint(user_id=uid,title='Fixture for algorithm validation',description='Projector needs replacement and parts' if delayed else 'Light needs simple bulb replacement',
                category='Classroom',department='IT Services',initial_department='Facilities',location='Test Block',severity='Critical',sla_hours=8,
                submitted_at=submitted,resolved_at=submitted+timedelta(hours=12 if delayed else 2),status='Resolved',is_demo=False)
            db.session.add(c)
        # Pending rows and demo rows must not become labels.
        db.session.add(Complaint(user_id=uid,title='pending',description='Pending fixture',category='Classroom',department='Facilities',initial_department='Facilities',location='Block',severity='High',sla_hours=24,is_demo=False))
        db.session.commit()
        f=ml.features(db.session.query(Complaint).first());assert 'resolved_at' not in f and 'status' not in f and f['department']=='Facilities'
        report=ml.train_model();assert report['mode']=='ml' and report['training_rows']==128 and report['holdout_rows']==32
        assert len(report['confusion_matrix'])==2 and 0<=report['f1']<=1
        result=ml.predict_delay(PAYLOAD,'Facilities',24);assert result['mode']=='ml' and 0<=result['probability']<=1

def test_role_management_and_security_headers(app):
    c=app.test_client();h=login(c,'admin')
    users=c.get('/api/users').json['users']; student=next(u for u in users if u['email']=='s@college.test')
    r=c.patch('/api/users/'+str(student['id']),headers=h,json={'role':'officer','department':'Library'});assert r.json['user']['department']=='Library'
    me=next(u for u in users if u['role']=='admin');assert c.patch('/api/users/'+str(me['id']),headers=h,json={'role':'student'}).status_code==400
    r=c.get('/');assert r.status_code==200 and "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
    c.post('/api/logout',headers=h,json={});assert c.get('/api/users').status_code==401
