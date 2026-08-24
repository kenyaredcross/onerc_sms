# Copyright (c) 2026, Kelvin Njenga and contributors
# See license.txt

"""Retire the SMS Campaign approval workflow installed by older releases.

Campaign authorization is the DocType's ordinary ``submit`` permission. An
active native Workflow suppresses Frappe's Submit button, and the old workflow
kept even its Approved state at docstatus 0. The result was an approved draft
with no legal route to submission and therefore no route to sending.

Called after every migrate so existing sites are repaired while fresh sites
remain unchanged. Workflow States and Actions are deliberately retained: they
are shared vocabularies and another workflow may use them.
"""

import frappe

WORKFLOW = "SMS Campaign Approval"


def remove() -> None:
	if not frappe.db.exists("Workflow", WORKFLOW):
		return

	frappe.delete_doc("Workflow", WORKFLOW, force=True, ignore_permissions=True)
