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
    portal='registrar' if name in ['s','s2'] else 'checker'
    c.environ_base['HTTP_X_PORTAL']=portal
    token=c.get('/api/session').json['csrf']
    r=c.post(f'/api/auth/{portal}/login',json={'email':name+'@college.test','password':'long-test-password'},headers={'X-CSRF-Token':token})
    assert r.status_code==200
    return {'X-CSRF-Token':r.json['csrf'],'X-Portal':portal}

PAYLOAD=dict(title='Projector is broken',description='The projector does not start during the morning lecture.',category='Classroom',location='Block A',severity='High',reported_impact='Students cannot see lecture slides.',reported_reason='Teaching is interrupted during lectures.',reported_cause='A loose cable is suspected, not confirmed.')

def test_production_requires_database(monkeypatch):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with pytest.raises(RuntimeError,match='DATABASE_URL'):
        create_app()

def test_auth_csrf_and_no_self_promotion(app):
    c=app.test_client()
    c.environ_base['HTTP_X_PORTAL']='registrar'
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
    r=off.patch(f'/api/complaints/{cid}',json={'message':'Complaint checked for referral','status':'Reviewed'},headers=fh); assert r.status_code==200
    admin=app.test_client(); ah=login(admin,'admin')
    assert admin.patch(f'/api/complaints/{cid}',json={'message':'IT team to inspect cable','department':'IT Services'},headers=ah).status_code==200
    visible_after_reassignment=off.get('/api/complaints').json['complaints']
    assert len(visible_after_reassignment)==1 and not visible_after_reassignment[0]['can_review']
    assert it.patch(f'/api/complaints/{cid}',json={'status':'Resolved','message':'Cable replaced and tested'},headers=ih).status_code==400
    r=it.patch(f'/api/complaints/{cid}/brief',json={'checker_cause':'Not established; inspect the display cable.','checker_effect':'Lecture visibility and delivery are affected.','checker_reason':'Restore access to teaching materials promptly.'},headers=ih)
    assert r.status_code==200 and r.json['complaint']['resolved_at'] is None
    assert r.json['complaint']['brief']['reviewed_by']=='it'
    assert r.json['complaint']['brief']['cause_verified'] is False
    r=it.patch(f'/api/complaints/{cid}',json={'status':'Forwarded','message':'Conveyed the target time and impact to IT staff.'},headers=ih)
    assert r.status_code==200 and r.json['complaint']['status']=='Forwarded'
    tracked=s.get('/api/complaints').json['complaints'][0]
    assert len(tracked['events'])==5 and tracked['brief']['reported_impact']==PAYLOAD['reported_impact']
    assert tracked['brief']['target_hours']==24 and not tracked['brief']['physical_resolution_confirmed']
    a=admin.get('/api/analytics');assert a.status_code==200 and a.json['summary']['resolved']==0
    with app.app_context(): assert db.session.get(Complaint,cid).initial_department=='Facilities'

def test_demo_isolation_and_model_fallback(app):
    with app.app_context():
        from seed import seed_demo
        seed_demo()
    c=app.test_client(); h=login(c,'s'); c.post('/api/complaints',json=PAYLOAD,headers=h)
    d=app.test_client();d.environ_base['HTTP_X_PORTAL']='checker';token=d.get('/api/session').json['csrf'];r=d.post('/api/auth/checker/demo',json={'role':'admin'},headers={'X-CSRF-Token':token});dh={'X-CSRF-Token':r.json['csrf']}
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

def test_separate_portal_logins_and_independent_sessions(app):
    c=app.test_client();student_headers=login(c,'s')
    assert c.post('/api/auth/registrar/login',headers=student_headers,json={'email':'off@college.test','password':'long-test-password'}).status_code==403
    assert c.post('/api/auth/registrar/demo',headers=student_headers,json={'role':'admin'}).status_code==403
    staff_headers=login(c,'off')
    assert c.get('/api/session',headers={'X-Portal':'registrar'}).json['user']['email']=='s@college.test'
    assert c.get('/api/session',headers={'X-Portal':'checker'}).json['user']['email']=='off@college.test'
    assert c.post('/api/auth/checker/login',headers=staff_headers,json={'email':'s@college.test','password':'long-test-password'}).status_code==403
    assert c.post('/api/register',headers=staff_headers,json={}).status_code==403
    assert c.post('/api/auth/registrar/login',headers=staff_headers,json={}).status_code==400
    c.post('/api/logout',headers=staff_headers,json={})
    assert c.get('/api/session',headers={'X-Portal':'checker'}).json['user'] is None
    assert c.get('/api/session',headers={'X-Portal':'registrar'}).json['user']['role']=='student'
    assert c.post('/api/complaints',headers=student_headers,json=PAYLOAD).status_code==201
    for path in ['/','/register-complaint','/complaint-checker']:
        assert c.get(path).status_code==200

def test_brief_access_controls_and_required_effects(app):
    s=app.test_client();sh=login(s,'s')
    bad=dict(PAYLOAD);bad.pop('reported_impact')
    assert s.post('/api/complaints',headers=sh,json=bad).status_code==400
    c=s.post('/api/complaints',headers=sh,json=PAYLOAD).json['complaint']
    assert c['brief']['assessment_source'].startswith('Category guidance')
    assert 'inspection' in c['brief']['possible_cause']
    body=dict(checker_cause='Not established; inspection needed.',checker_reason='Teaching is interrupted.',checker_effect='Students cannot view learning materials.')
    assert s.patch(f'/api/complaints/{c["id"]}/brief',headers=sh,json=body).status_code==403
    other=app.test_client();oh=login(other,'it')
    assert other.patch(f'/api/complaints/{c["id"]}/brief',headers=oh,json=body).status_code==404

def test_staff_signup_login_and_administrator_approval(app):
    staff=app.test_client();staff.environ_base['HTTP_X_PORTAL']='checker'
    token=staff.get('/api/session').json['csrf']
    body=dict(name='New Staff Member',email='staff@college.test',password='strong-staff-password',department='Facilities',role='admin',is_demo=True)
    r=staff.post('/api/auth/checker/register',json=body,headers={'X-CSRF-Token':token})
    assert r.status_code==201 and r.json['user']['role']=='pending_staff'
    assert not r.json['user']['is_demo'] and r.json['user']['approval_status']=='pending'
    h={'X-CSRF-Token':r.json['csrf']}
    assert staff.get('/api/session').json['user']['name']=='New Staff Member'
    for path in ['complaints','analytics','users','model']:
        assert staff.get('/api/'+path).status_code==403
    assert staff.post('/api/predict',json=PAYLOAD,headers=h).status_code==403
    assert staff.patch('/api/users/'+str(r.json['user']['id']),json={'role':'admin'},headers=h).status_code==403
    staff.post('/api/logout',headers=h,json={})
    token=staff.get('/api/session').json['csrf']
    r=staff.post('/api/auth/checker/login',json={'email':body['email'],'password':body['password']},headers={'X-CSRF-Token':token})
    assert r.status_code==200 and r.json['user']['role']=='pending_staff'
    admin=app.test_client();ah=login(admin,'admin')
    pending=next(u for u in admin.get('/api/users').json['users'] if u['email']==body['email'])
    assert pending['id'] in [u['id'] for u in admin.get('/api/staff-requests').json['requests']]
    assert pending['department']=='Facilities'
    r=admin.patch('/api/users/'+str(pending['id']),json={'role':'officer','department':'Facilities'},headers=ah)
    assert r.status_code==200 and r.json['user']['approval_status']=='active'
    assert staff.get('/api/session').json['user']['role']=='officer'
    assert staff.get('/api/complaints').status_code==200
    student=app.test_client();sh=login(student,'s')
    assert student.post('/api/auth/checker/register',json=body,headers=sh).status_code==403

def test_every_approved_staff_and_admin_sees_student_complaint(app):
    student=app.test_client();sh=login(student,'s')
    cid=student.post('/api/complaints',headers=sh,json=PAYLOAD).json['complaint']['id']
    for name in ['off','it','admin']:
        c=app.test_client();h=login(c,name)
        row=next(r for r in c.get('/api/complaints').json['complaints'] if r['id']==cid)
        assert row['can_review']==(name in ['off','admin'])
        if name!='admin': assert c.get('/api/staff-requests').status_code==403
    other=app.test_client();login(other,'s2');assert other.get('/api/complaints').json['complaints']==[]

def test_exact_sample_cleanup_preserves_user_submissions(app):
    from seed import seed_demo
    from maintenance import remove_assistant_samples,VERIFICATION
    from app import ComplaintBrief
    with app.app_context():
        seed_demo()
        demo_student=db.session.query(User).filter_by(email='student@demo.campus').one()
        real_student=db.session.query(User).filter_by(email='s@college.test').one()
        for cid,(title,description) in VERIFICATION.items():
            db.session.add(Complaint(id=cid,user_id=demo_student.id,title=title,description=description,category='Classroom',department='Facilities',initial_department='Facilities',location='Demo verification room',severity='High',sla_hours=24,is_demo=True))
        db.session.flush();db.session.add(ComplaintBrief(complaint_id=98,reported_impact='Test fixture'))
        for cid,uid,demo in [(99,demo_student.id,True),(100,real_student.id,False)]:
            db.session.add(Complaint(id=cid,user_id=uid,title='Actual student submitted concern',description='A genuine user report that must be preserved.',category='Classroom',department='Facilities',initial_department='Facilities',location='Block A',severity='High',sla_hours=24,is_demo=demo))
        db.session.commit()
        report=remove_assistant_samples();assert report['removed_count']==98
        assert [c.id for c in db.session.query(Complaint).order_by(Complaint.id)]==[99,100]
        assert db.session.get(ComplaintBrief,98) is None
        assert remove_assistant_samples()==report

def test_live_mode_disables_demo_and_explicit_admin_login(app):
    from seed import seed_demo
    with app.app_context(): seed_demo()
    demo=app.test_client();demo.environ_base['HTTP_X_PORTAL']='checker'
    token=demo.get('/api/session').json['csrf'];r=demo.post('/api/auth/checker/demo',headers={'X-CSRF-Token':token},json={'role':'admin'})
    assert r.status_code==200
    app.config['DEMO_ENABLED']=False
    assert demo.get('/api/session').json['user'] is None
    assert demo.get('/api/users').status_code==401
    token=demo.get('/api/session').json['csrf']
    assert demo.post('/api/auth/checker/demo',headers={'X-CSRF-Token':token},json={'role':'admin'}).status_code==404
    assert demo.post('/api/auth/checker/login',headers={'X-CSRF-Token':token},json={'email':'off@college.test','password':'long-test-password','login_as':'administrator'}).status_code==403
    assert demo.post('/api/auth/checker/login',headers={'X-CSRF-Token':token},json={'email':'admin@college.test','password':'long-test-password','login_as':'administrator'}).status_code==200
