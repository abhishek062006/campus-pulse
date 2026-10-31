"""Transparent category guidance, never a diagnosis or a promise of repair."""
from datetime import timedelta

GUIDANCE={
 'Classroom': ('Equipment wear, a power-supply issue, or a loose connection; an inspection is needed.', 'Teaching sessions, presentations and student concentration may be affected.', 'Restore access to classroom facilities so teaching can continue without interruption.'),
 'Hostel': ('A plumbing, supply, pump or maintenance issue; hostel staff must verify the cause.', 'Residents’ water access, hygiene, comfort or daily routines may be affected.', 'Protect residents’ access to essential hostel services.'),
 'Internet': ('Network coverage, equipment, congestion or configuration issues; IT must investigate.', 'Course access, research, online classes and submissions may be affected.', 'Maintain dependable access to learning and academic services.'),
 'Library': ('Stock availability, circulation processes or library service constraints; staff must verify.', 'Study, reference access and research may be affected.', 'Support equitable access to academic resources.'),
 'Transportation': ('Vehicle availability, route timing or operating conditions; transport staff must verify.', 'Attendance, punctuality and commuting reliability may be affected.', 'Help students reach campus and scheduled classes reliably.'),
 'Cleanliness': ('Collection schedules, cleaning coverage or a recurring waste source; inspection is needed.', 'Hygiene, shared-space use and comfort may be affected.', 'Keep shared campus areas hygienic and usable.'),
 'Canteen': ('Food handling, storage, cleaning or service practices; canteen staff must investigate.', 'Food access, hygiene and student wellbeing may be affected.', 'Refer reported food and hygiene concerns promptly for inspection.'),
 'Laboratory': ('Equipment faults, software configuration or maintenance needs; lab staff must inspect.', 'Practical learning, experiments and access to equipment may be affected.', 'Support safe, reliable access to practical learning facilities.'),
 'Drinking Water': ('A supply interruption, dispenser fault or filter maintenance issue; staff must inspect.', 'Access to drinking water and daily campus activities may be affected.', 'Protect access to an essential campus service.'),
 'Washroom': ('A plumbing, fixture, water-supply or cleaning issue; inspection is needed.', 'Sanitation, accessibility, hygiene and water use may be affected.', 'Keep essential sanitation facilities accessible and usable.')}

def build_brief(c,b):
    cause,effect,reason=GUIDANCE.get(c.category,('Not established; department inspection is needed.','Effects need clarification from the complainant.','The responsible department should assess the concern.'))
    iso=lambda d: d.isoformat()+'Z' if d else None
    return dict(target_at=iso(c.submitted_at+timedelta(hours=c.sla_hours)),target_hours=c.sla_hours,
        target_basis=f'{c.severity} severity policy: {c.sla_hours} continuous hours from submission. This is a recommended service target, not a confirmed completion time.',
        reported_impact=b.reported_impact if b else '',reported_cause=b.reported_cause if b else '',reported_reason=b.reported_reason if b else '',
        possible_cause=b.checker_cause if b and b.checker_cause else cause,
        effects=b.checker_effect if b and b.checker_effect else (b.reported_impact if b and b.reported_impact else effect),
        why_attention=b.checker_reason if b and b.checker_reason else (b.reported_reason if b and b.reported_reason else reason),
        assessment_source='Checker assessment' if b and b.reviewed_at else 'Category guidance and complainant report; not verified',
        reviewed_by=b.reviewed_by if b else None,reviewed_at=iso(b.reviewed_at) if b else None,
        cause_verified=False,physical_resolution_confirmed=False)
