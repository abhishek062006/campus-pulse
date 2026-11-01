# Campus Pulse

A full-stack college complaint conveyance system with two separate login portals: **Complaint registration** (`/register-complaint`) for students and **Complaint checker** (`/complaint-checker`) for authorized department officers and college administrators. Flask, SQLAlchemy, PostgreSQL (SQLite locally), scikit-learn, and a responsive browser interface.

The platform conveys, assesses and forwards complaints. It does not perform repairs or allow new complaints to be marked physically resolved.

## Run locally

Use Python 3.12. Create a virtual environment, install `requirements.txt`, then run `flask --app wsgi run`. Open http://127.0.0.1:5000. Run `pytest -q` for integration and ML tests.

Set `SECRET_KEY` to a random secret and `APP_ENV=production` for HTTPS deployment. Set `DATABASE_URL` to a PostgreSQL URL for persistent hosting. SQLite is for local use only. Start production with `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`. One worker shares the in-memory model and rate limiter. Scale only after moving both to shared storage.

## Accounts and demo

Each portal has its own login and create-account form, plus matching demo accounts. Student credentials are rejected by the checker; staff credentials are rejected by registration. Sessions and CSRF tokens are independently namespaced, so both portals can stay signed in in separate browser tabs, and logging out of one does not log out of the other. Staff can sign up with name, staff email, department and password; they are signed in immediately to an approval-status screen. New staff always receive the `pending_staff` role, regardless of submitted role or demo fields, and cannot access complaints, analytics, people or prediction APIs. A real administrator verifies their identity in People & access and assigns the officer role and department. The staff member can check approval status or sign in again to enter their department dashboard. Demo administrators cannot approve real staff. Existing accounts need no schema changes. Demo accounts have random, undisclosed passwords and can access only shared sample records. Do not enter personal information in the demo. Demo officers belong to Facilities. Demo administrators can reassign sample complaints but cannot change permissions or train on real records. Ninety-six illustrative records are seeded once. Set `SEED_DEMO=false` to disable seeding on a fresh database; existing demo accounts must be removed separately if retiring the demo.

Real students register normally; role and demo status are always set by the server. Set `ADMIN_EMAIL` and a random `ADMIN_PASSWORD` (12+ characters) to bootstrap the first real college administrator on startup. No default administrator password exists. The administrator can assign registered people to officer departments or admin roles through People & access. Passwords use Werkzeug scrypt; sessions are signed, HTTP-only, same-site cookies, Secure in production. CSRF tokens, authorization on every operation, input limits, rate limiting, parameterized queries, strict CSP, and escaped UI output protect the application. CSV cells escape spreadsheet formulas.

This prototype has no email verification, password-reset email, attachments, external notifications, or institutional SSO. Configure institutional identity and recovery before an operational college rollout. The prototype has one college scope; it is not a multi-tenant SaaS. Staff can read complaints appropriate to their assigned role. Be clear about this access policy to students.

## Workflow

Students submit title, text, category, location, severity, **who or what is affected**, and **why attention is needed**. A suspected cause is optional and is never treated as verified. Category routes the initial department. The recommended target time is frozen at submission: Low 168h, Medium 72h, High 24h, Critical 8h. Times use continuous UTC hours; a target is not a guaranteed completion date.

The checker sees a four-part brief: recommended completion/attention date and time, reason for attention, possible cause, and affected people/services. Initial suggestions use transparent category rules and the complainant report, not an invented ML diagnosis. Checkers can record their assessment and append review or forwarding notes. Causes stay explicitly unverified. Statuses for new complaints are **Submitted**, **Reviewed**, and **Forwarded**. Neither an assessment nor forwarding sets a resolution timestamp. The API rejects attempts to mark a complaint Resolved.

Administrators can reassign departments, manage staff, inspect workload and historical delay analytics, and train an existing historical-data model when eligible. Previous resolution records are retained as **Historical outcome** records, without claiming the new workflow repaired them. They cannot be changed into new resolution events. Original student context remains available separately from checker assessments. A new additive `complaint_brief` table stores the context and assessments without altering or deleting existing complaints. Users can filter, search and export authorized records.

## Honest ML

No model is fabricated from demo records, category guidance, checker assessments, or forwarded status. The initial interface explicitly shows **Fallback / Not assessed**, no delay probability, and the severity target. An administrator can train only when at least 100 legitimate historical real resolved complaints exist, with 60+ chronological training rows and 20+ holdout rows. Both classes require at least 15 training and 5 holdout examples. Open, reviewed and forwarded complaints are not negative labels. New conveyance-only activity cannot produce resolution labels; future outcome data would need a separately authorized, verified historical-data integration.

Label: actual resolution duration exceeds the SLA frozen at submission. Features: complaint text, initial category and department, location, severity, submission hour/weekday and frozen SLA. Current status, current/reassigned department, resolution duration, elapsed time, and updates are excluded. The newest 20% of submissions form a holdout; a training record must have been resolved before that holdout starts. TF-IDF, categorical encoding and scaling fit only on training data. Logistic regression reports precision, recall, F1, accuracy, ROC AUC, majority baseline and confusion matrix. Model reports are saved in the database; the fitted model remains in memory and requires explicit retraining after restart. No unsafe serialized model loading.

Results describe associations, not causal factors. Uncalibrated estimates need human review. Monitor class balance, drift, outcome integrity, sample size and subgroup performance before using predictions to prioritize work. Baseline is a comparator, not a claim of improvement. Automated integration tests exercise the real training pipeline with **test-only** artificial fixtures; none are inserted into the deployed database.

## Source requirement limitation

The referenced `/mnt/data/PE-I_ADS-MID-1-Prototype.pdf` was not accessible in this Windows workspace, and no prior-chat retrieval tool was available. Implementation follows the supplied problem statement and objectives; PDF-specific requirements have not been verified.

## Deployment

`render.yaml` defines a web service and PostgreSQL. Choose resources in the same region and keep credentials in Render environment variables, never Git. Free PostgreSQL has provider lifecycle/expiry limits; use a durable paid database and backups for operational data. `/health` checks app/database connectivity. The Blueprint provisions `admin@campuspulse.local` with a platform-generated password stored privately in Render's `ADMIN_PASSWORD` environment variable. The owner can retrieve it in the Render dashboard. Production startup refuses a missing `DATABASE_URL`; do not use ephemeral local SQLite for cloud records. When resources are created manually, the Render dashboard is the configuration source of truth; do not apply the Blueprint over them without reviewing resource matching.
