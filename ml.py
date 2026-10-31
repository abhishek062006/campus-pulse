"""Delay prediction. Never consume post-submission features or synthetic labels."""
import threading
from datetime import timedelta
from sqlalchemy import select
from app import db, Complaint, ModelVersion, now

_model=None
_report=None
_lock=threading.Lock()
MIN_ROWS=100

def eligible():
    return list(db.session.scalars(select(Complaint).where(Complaint.is_demo.is_(False),Complaint.resolved_at.is_not(None)).order_by(Complaint.submitted_at,Complaint.id)))

def features(c):
    # Frozen fields only. Current department/status, updates, elapsed and resolution time are excluded.
    return dict(text=c.description,category=c.category,department=c.initial_department,location=c.location,
                severity=c.severity,hour=c.submitted_at.hour,weekday=c.submitted_at.weekday(),sla_hours=c.sla_hours)

def get_report():
    if _report: return _report
    last=db.session.scalar(select(ModelVersion).order_by(ModelVersion.id.desc()))
    return dict(mode='fallback',reason='No trained model loaded. Administrator must train after enough verified, real resolved complaints exist.',
        eligible_real_rows=len(eligible()),minimum_rows=MIN_ROWS,last_training_report=last.report if last else None,
        features=['Complaint text','Initial category & department','Location','Severity','Submission hour & weekday','SLA at submission'])

def train_model():
    global _model,_report
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.compose import ColumnTransformer
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix, roc_auc_score
    import numpy as np
    # Model training and replacement are serialized; failures cannot replace the active model.
    with _lock:
        rows=eligible(); split=int(len(rows)*.8)
        if len(rows)<MIN_ROWS: return dict(mode='fallback',error=f'Need at least {MIN_ROWS} real resolved complaints; found {len(rows)}.',eligible_real_rows=len(rows))
        test=rows[split:]; cutoff=test[0].submitted_at
        # Training labels must have been available BEFORE the held-out submission period.
        training=[c for c in rows[:split] if c.resolved_at<cutoff]
        label=lambda c: int((c.resolved_at-c.submitted_at).total_seconds()>c.sla_hours*3600)
        yt=[label(c) for c in training]; yv=[label(c) for c in test]
        if len(training)<60 or len(test)<20 or min(yt.count(0),yt.count(1))<15 or min(yv.count(0),yv.count(1))<5:
            return dict(mode='fallback',error='Need at least 60 chronological training rows, 20 holdout rows, 15 training examples and 5 holdout examples of each class after label-availability filtering.')
        columns=['text','category','department','location','severity','hour','weekday','sla_hours']
        matrix=lambda items: np.array([[features(c)[k] for k in columns] for c in items],dtype=object)
        transform=ColumnTransformer([('text',TfidfVectorizer(max_features=1500,ngram_range=(1,2),min_df=2),0),
            ('categorical',OneHotEncoder(handle_unknown='ignore'),[1,2,3,4]),('numeric',StandardScaler(),[5,6,7])])
        model=Pipeline([('features',transform),('classifier',LogisticRegression(class_weight='balanced',max_iter=1000,random_state=42))])
        model.fit(matrix(training),yt); pred=model.predict(matrix(test)); prob=model.predict_proba(matrix(test))[:,1]
        report=dict(mode='ml',algorithm='TF-IDF + one-hot encoding + logistic regression',trained_at=now().isoformat()+'Z',
            eligible_real_rows=len(rows),training_rows=len(training),holdout_rows=len(test),holdout_start=cutoff.isoformat()+'Z',
            accuracy=round(accuracy_score(yv,pred),3),precision=round(precision_score(yv,pred,zero_division=0),3),
            recall=round(recall_score(yv,pred,zero_division=0),3),f1=round(f1_score(yv,pred,zero_division=0),3),roc_auc=round(roc_auc_score(yv,prob),3),
            confusion_matrix=confusion_matrix(yv,pred,labels=[0,1]).tolist(),majority_baseline=round(max(yv.count(0),yv.count(1))/len(yv),3),
            label='Delayed when actual resolution duration exceeds severity SLA recorded at submission.',
            validation='Chronological 80/20 split; training resolution must precede first holdout submission. Transformations fit only on training data. Demo rows excluded.',
            limitation='Observational associations, not causes. Holdout scores do not guarantee future performance. No calibration claim. Model stays in memory; retrain after restart.')
        db.session.add(ModelVersion(report=report)); db.session.commit(); _model=model; _report=report
        return report

def predict_delay(d,department,sla):
    from app import SLA
    if _model is None:
        return dict(mode='fallback',classification='Not assessed',probability=None,
            explanation=f'No validated model is available. The {d["severity"].lower()} severity target is {sla} hours. Track the due date; no delay probability is inferred from demo data.',sla_hours=sla)
    import numpy as np
    t=now(); x=np.array([[d['description'],d['category'],department,d['location'],d['severity'],t.hour,t.weekday(),sla]],dtype=object)
    p=float(_model.predict_proba(x)[0,1])
    return dict(mode='ml',classification='Delayed' if p>=.5 else 'Not Delayed',probability=round(p,3),sla_hours=sla,
        explanation='Statistical estimate from real resolved complaints. Probability is uncalibrated; review alongside severity and service targets.')
