# Campus Pulse

A full-stack college complaint tracker with Student, Department Officer, and College Administrator workspaces. Flask, SQLAlchemy, PostgreSQL (SQLite locally), scikit-learn, and a responsive, dependency-free browser interface.

## Run locally

Use Python 3.12. Create a virtual environment, install `requirements.txt`, then run `flask --app wsgi run`. Open http://127.0.0.1:5000. Run `pytest -q` for integration and ML tests.

Set `SECRET_KEY` to a random secret and `APP_ENV=production` for HTTPS deployment. Set `DATABASE_URL` to a PostgreSQL URL for persistent hosting. SQLite is for local use only. Start production with `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app`. One worker shares the in-memory model and rate limiter. Scale only after moving both to shared storage.

## Accounts and demo

The sign-in page offers one-click demo roles. These have random, undisclosed passwords and can access only sample records. Demo visitors share the same sample workspace; do not enter personal information there. Demo officers belong to Facilities. Demo administrators can reassign sample complaints but cannot change permissions or train on real records. Ninety-six illustrative records are seeded once. Set `SEED_DEMO=false` to disable seeding on a fresh database; existing demo accounts must be removed separately if retiring the demo.

Real students register normally; role and demo status are always set by the server. Set `ADMIN_EMAIL` and a random `ADMIN_PASSWORD` (12+ characters) to bootstrap the first real college administrator on startup. No default administrator password exists. The administrator can assign registered people to officer departments or admin roles through People & access. Passwords use Werkzeug scrypt; sessions are signed, HTTP-only, same-site cookies, Secure in production. CSRF tokens, authorization on every operation, input limits, rate limiting, parameterized queries, strict CSP, and escaped UI output protect the application. CSV cells escape spreadsheet formulas.

This prototype has no email verification, password-reset email, attachments, external notifications, or institutional SSO. Configure institutional identity and recovery before an operational college rollout. The prototype has one college scope; it is not a multi-tenant SaaS. Staff can read complaints appropriate to their assigned role. Be clear about this access policy to students.

## Workflow

Students submit title, text, category, location and severity. Category routes the initial department. Target time is frozen at submission: Low 168h, Medium 72h, High 24h, Critical 8h. Times use continuous UTC hours, not business hours. Officers update status and append public progress notes for their department. Administrators can reassign departments, manage staff, analyze category/department delays and train a model. Resolved complaints remain closed to avoid silently changing training outcomes; recurring issues are new complaints. Users can filter, search and export their authorized records.

## Honest ML

No model is fabricated from demo records. The initial interface explicitly shows **Fallback / Not assessed**, no delay probability, and the severity target. An administrator can train only when at least 100 real resolved complaints exist, with 60+ chronological training rows and 20+ holdout rows. Both classes require at least 15 training and 5 holdout examples. Open complaints are not negative labels.

Label: actual resolution duration exceeds the SLA frozen at submission. Features: complaint text, initial category and department, location, severity, submission hour/weekday and frozen SLA. Current status, current/reassigned department, resolution duration, elapsed time, and updates are excluded. The newest 20% of submissions form a holdout; a training record must have been resolved before that holdout starts. TF-IDF, categorical encoding and scaling fit only on training data. Logistic regression reports precision, recall, F1, accuracy, ROC AUC, majority baseline and confusion matrix. Model reports are saved in the database; the fitted model remains in memory and requires explicit retraining after restart. No unsafe serialized model loading.

Results describe associations, not causal factors. Uncalibrated estimates need human review. Monitor class balance, drift, outcome integrity, sample size and subgroup performance before using predictions to prioritize work. Baseline is a comparator, not a claim of improvement. Automated integration tests exercise the real training pipeline with **test-only** artificial fixtures; none are inserted into the deployed database.

## Source requirement limitation

The referenced `/mnt/data/PE-I_ADS-MID-1-Prototype.pdf` was not accessible in this Windows workspace, and no prior-chat retrieval tool was available. Implementation follows the supplied problem statement and objectives; PDF-specific requirements have not been verified.

## Deployment

`render.yaml` defines a web service and PostgreSQL. Choose resources in the same region and keep credentials in Render environment variables, never Git. Free PostgreSQL has provider lifecycle/expiry limits; use a durable paid database and backups for operational data. `/health` checks app/database connectivity. The Blueprint provisions `admin@campuspulse.local` with a platform-generated password stored privately in Render's `ADMIN_PASSWORD` environment variable. The owner can retrieve it in the Render dashboard. Production startup refuses a missing `DATABASE_URL`; do not use ephemeral local SQLite for cloud records. When resources are created manually, the Render dashboard is the configuration source of truth; do not apply the Blueprint over them without reviewing resource matching.
