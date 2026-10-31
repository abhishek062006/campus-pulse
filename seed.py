import random
import secrets
from datetime import timedelta
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from app import db, User, Complaint, Event, CATEGORIES, SLA, now

def seed_demo():
    if db.session.scalar(select(User).where(User.email=='admin@demo.campus')): return
    rng=random.Random(42)
    people={}
    for role,name,dep in [('student','Alex Morgan',None),('officer','Priya Shah','Facilities'),('admin','Jordan Lee',None)]:
        u=User(name=name,email=f'{role}@demo.campus',password=generate_password_hash(secrets.token_hex(32)),role=role,department=dep,is_demo=True)
        db.session.add(u); db.session.flush(); people[role]=u
    examples={
        'Classroom':('Projector flickering during lectures','The projector in the lecture hall flickers and disconnects during presentations.'),
        'Hostel':('Water supply interrupted in hostel','The hostel water supply stops every morning. Please inspect the pump and tank.'),
        'Internet':('Wi-Fi drops near the study area','The campus wireless network disconnects repeatedly and prevents access to course materials.'),
        'Library':('Reference books unavailable','The reference copies required for this semester have been unavailable for several weeks.'),
        'Transportation':('College bus arrives late','The morning bus arrives after the first lecture begins. Please review the route timing.'),
        'Cleanliness':('Waste bins need clearing','Waste bins near the academic block are overflowing and need regular collection.'),
        'Canteen':('Canteen tables need cleaning','Tables and the food serving counter need more frequent cleaning during lunch hours.'),
        'Laboratory':('Computers fail to start in lab','Several computers in the laboratory do not start and students cannot complete practical work.'),
        'Drinking Water':('Drinking water filter needs repair','The drinking water dispenser has low flow and the filter indicator is red.'),
        'Washroom':('Washroom tap leaking','A washroom tap has been leaking continuously. Please repair the fitting and check water pressure.')}
    for i in range(96):
        category=list(CATEGORIES)[i%len(CATEGORIES)]; title,desc=examples[category]; severity=rng.choice(list(SLA)); sla=SLA[severity]
        submitted=now()-timedelta(hours=rng.randint(3,720)); resolved=None
        if i%4!=0:
            duration=sla*rng.uniform(.2,2.0)
            if submitted+timedelta(hours=duration)<now(): resolved=submitted+timedelta(hours=duration)
        c=Complaint(user_id=people['student'].id,title=title,description=desc,category=category,department=CATEGORIES[category],initial_department=CATEGORIES[category],
            location=rng.choice(['Academic Block A','North Hostel','Central Campus','Science Block','Library Wing']),severity=severity,
            submitted_at=submitted,resolved_at=resolved,status='Resolved' if resolved else ('In Progress' if i%2 else 'Submitted'),sla_hours=sla,is_demo=True)
        db.session.add(c); db.session.flush(); db.session.add(Event(complaint_id=c.id,actor='Demo student',message='Sample complaint submitted.',created_at=submitted))
        if resolved: db.session.add(Event(complaint_id=c.id,actor='Demo department',message='Resolved: Work completed and checked.',created_at=resolved))
    db.session.commit()
