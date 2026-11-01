"""One-time user-requested removal of exactly identified assistant-created samples."""
from sqlalchemy import select, delete
from app import db,Complaint,ComplaintBrief,Event,MaintenanceRun

KEY='remove-assistant-seed-and-verification-v1'
SEEDS={
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
VERIFICATION={
 97:('Deployment verification: classroom light','Synthetic deployment verification complaint. Please check the classroom light fitting.'),
 98:('Portal verification: classroom display','Synthetic portal verification: the classroom projector disconnects during a lecture.')}

def remove_assistant_samples():
    previous=db.session.get(MaintenanceRun,KEY)
    if previous: return previous.report
    removed=[]
    for c in db.session.scalars(select(Complaint).where(Complaint.is_demo.is_(True))).all():
        seeded=(1<=c.id<=96 and (c.title,c.description)==SEEDS.get(c.category) and any(e.actor=='Demo student' and e.message=='Sample complaint submitted.' for e in c.events))
        verification=(c.id in VERIFICATION and (c.title,c.description)==VERIFICATION[c.id] and c.location=='Demo verification room')
        if seeded or verification:
            removed.append(c.id)
            db.session.execute(delete(ComplaintBrief).where(ComplaintBrief.complaint_id==c.id))
            db.session.delete(c)
    report={'removed_ids':removed,'removed_count':len(removed),'scope':'Exact assistant sample fingerprints only; user submissions preserved.'}
    db.session.add(MaintenanceRun(key=KEY,report=report));db.session.commit()
    return report
