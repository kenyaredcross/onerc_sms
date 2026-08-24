# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""Wires up the SMS Campaign Approval workflow the app's fixtures and role
permissions already assume exists.

`SMS Campaign Manager` and `SMS Campaign Approver` ship as ordinary Roles, and
`SMS Campaign.json` already grants submit to the first and read/write to the
second — but nothing ever created the Workflow record connecting them to
`workflow_state`, so today submitting a campaign skips approval entirely.

Idempotent, and safe to re-run: it only creates records that do not yet exist,
so a site that has since customised the workflow by hand is left alone.
Called from `after_migrate`, the same point `vmmsx.staff.services.permissions
.install` runs from.

**Deliberately flat, not geo-scoped.** An Approver may approve any campaign
anywhere, not only ones addressed to their own area — unlike vmmsx's own
approval engine, which routes by Geo Assignment. Frappe's native Workflow
cannot express "the specific person this resolved to," only "holds this
role" (see vmmsx's own approval-engine docs for the same argument), and a
flat pool was the deliberate choice here rather than building a parallel
routing layer for one app.

**Approval and Frappe's own Submit are kept apart.** Every workflow state
below stays at `doc_status = 0` — reaching "Approved" only moves
`workflow_state`. What actually stops the native Submit button short of
Approved is `SMSCampaign.validate_approval()` in `sms_campaign.py`, so the
existing `on_submit()` / `send_campaign()` path needed no changes at all: by
the time it runs, approval has already been checked.
"""

import frappe

DOCTYPE = "SMS Campaign"
WORKFLOW = "SMS Campaign Approval"

MANAGER = "SMS Campaign Manager"
APPROVER = "SMS Campaign Approver"

STATES = [
	{"state": "Draft", "allow_edit": MANAGER},
	{"state": "Pending", "allow_edit": APPROVER},
	{"state": "Approved", "allow_edit": APPROVER},
	{"state": "Rejected", "allow_edit": MANAGER},
]

TRANSITIONS = [
	{"state": "Draft", "action": "Submit for Approval", "next_state": "Pending", "allowed": MANAGER},
	{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": APPROVER},
	{"state": "Pending", "action": "Reject", "next_state": "Rejected", "allowed": APPROVER},
	{"state": "Rejected", "action": "Resubmit", "next_state": "Pending", "allowed": MANAGER},
]


def install() -> None:
	"""Create the workflow's dependencies and the workflow itself, if missing.

	    bench --site <site> execute onerc_sms.setup.workflow.install
	"""
	_ensure_actions()
	_ensure_states()
	_ensure_workflow()


def _ensure_actions() -> None:
	for row in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", row["action"]):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": row["action"]}
			).insert(ignore_permissions=True)


def _ensure_states() -> None:
	for row in STATES:
		if not frappe.db.exists("Workflow State", row["state"]):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": row["state"]}).insert(
				ignore_permissions=True
			)


def _ensure_workflow() -> None:
	if frappe.db.exists("Workflow", WORKFLOW):
		return

	doc = frappe.new_doc("Workflow")
	doc.workflow_name = WORKFLOW
	doc.document_type = DOCTYPE
	doc.workflow_state_field = "workflow_state"
	doc.is_active = 1
	doc.send_email_alert = 0

	for row in STATES:
		doc.append("states", {"state": row["state"], "doc_status": "0", "allow_edit": row["allow_edit"]})

	for row in TRANSITIONS:
		doc.append(
			"transitions",
			{
				"state": row["state"],
				"action": row["action"],
				"next_state": row["next_state"],
				"allowed": row["allowed"],
			},
		)

	doc.insert(ignore_permissions=True)
