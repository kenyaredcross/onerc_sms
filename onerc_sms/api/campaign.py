# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""What the SMS Campaign form asks the server.

Two of these feed pickers on the form -- which field holds a phone number, and
which fields a filter row may name -- and two act on a campaign.

**Every endpoint that touches a campaign checks permission on it.**
`frappe.get_doc` performs no permission check unless it is asked to, so a
whitelisted method that only calls it is reachable by any signed-in session,
including a volunteer on the portal. `preview_campaign` returns real phone
numbers and the exact text about to be sent to them; `send_now` spends the
society's credit. Neither is something to leave open.
"""

import frappe
from frappe import _
from frappe.model import default_fields, no_value_fields

CAMPAIGN_DOCTYPE = "SMS Campaign"

#: Field types that can hold a phone number. `Phone` is the obvious one; `Data`
#: is here because most societies' contact fields predate it.
PHONE_FIELDTYPES = ("Data", "Phone")

#: How many of a campaign's recipients the preview shows. Enough to see that the
#: template rendered, not enough to be a way of exporting the audience.
PREVIEW_SAMPLE = 3


@frappe.whitelist()
def get_doctype_fields(doctype):
	"""Fields on this doctype that could hold a phone number.

	Some doctypes carry no phone field of their own -- VMMS Volunteer and VMMS
	Member both hold contact details on the linked Red Profile instead -- so
	Link fields are probed one level deep too, as "linkfield.target".
	`SMSCampaign._split_phone_field()` on the backend and the keyword filter in
	sms_campaign.js both already understand a dotted phone_field; this is what
	lets the campaign builder offer one instead of nothing.
	"""
	frappe.has_permission(CAMPAIGN_DOCTYPE, ptype="read", throw=True)

	meta = frappe.get_meta(doctype)

	fields = [field.fieldname for field in meta.fields if field.fieldtype in PHONE_FIELDTYPES]

	for field in meta.fields:
		if field.fieldtype != "Link" or not field.options:
			continue

		try:
			linked_meta = frappe.get_meta(field.options)
		except frappe.DoesNotExistError:
			continue

		fields += [
			f"{field.fieldname}.{linked_field.fieldname}"
			for linked_field in linked_meta.fields
			if linked_field.fieldtype in PHONE_FIELDTYPES
		]

	return fields


@frappe.whitelist()
def get_filter_fields(doctype):
	"""Every field a filter row may name on this doctype, for the grid's picker.

	Returned as `{value, label, description}` so the Autocomplete shows a person
	the field's **label** -- "Enrolment Date" -- while storing its fieldname, and
	puts the fieldname and type underneath. Somebody building a filter knows the
	form, not the schema, and asking them to remember that Branch is stored as
	`geo_node` is how filters end up wrong.

	The standard columns every doctype has are appended, because filtering on
	`creation` is one of the more useful things a coordinator can do and
	`Meta.fields` never mentions it.
	"""
	frappe.has_permission(CAMPAIGN_DOCTYPE, ptype="read", throw=True)

	meta = frappe.get_meta(doctype)

	fields = [
		{
			"value": field.fieldname,
			"label": field.label or field.fieldname,
			"description": _describe(field),
		}
		for field in meta.fields
		if field.fieldtype not in no_value_fields and field.fieldname
	]

	fields += [
		{"value": name, "label": name, "description": _("standard field")}
		for name in default_fields
		if name not in ("doctype", "idx")
	]

	fields.sort(key=lambda field: field["label"].lower())

	return fields


def _describe(field) -> str:
	"""The line under a field in the picker: its fieldname, and what it holds."""
	if field.fieldtype == "Link":
		return f"{field.fieldname} · links to {field.options}"

	if field.fieldtype == "Select":
		choices = [option for option in (field.options or "").split("\n") if option.strip()]
		joined = ", ".join(choices[:4]) + ("…" if len(choices) > 4 else "")
		return f"{field.fieldname} · one of {joined}" if joined else field.fieldname

	return f"{field.fieldname} · {field.fieldtype}"


@frappe.whitelist()
def send_now(campaign):
	"""Dispatch a submitted campaign immediately, rather than at its scheduled time."""
	doc = _get_checked(campaign, "submit")

	if doc.docstatus != 1:
		frappe.throw(_("Campaign must be submitted before sending."))

	doc.send_campaign()
	frappe.db.commit()

	return "OK"


@frappe.whitelist()
def preview_campaign(campaign):
	"""How many people this reaches, and what the first few of them would read.

	The one act in this app that cannot be undone is sending, and a number
	somebody can read before pressing anything is what makes "this is going to
	4,300 people" a decision rather than a discovery.
	"""
	doc = _get_checked(campaign, "read")

	recipients = doc.resolve_contacts()
	recipients = doc.run_pipeline(recipients)

	return {
		"total": len(recipients),
		"preview": [
			{"phone": recipient["phone"], "message": recipient["message"]}
			for recipient in recipients[:PREVIEW_SAMPLE]
		],
	}


def _get_checked(campaign: str, ptype: str):
	"""Load the campaign and insist the caller may do this to it."""
	doc = frappe.get_doc(CAMPAIGN_DOCTYPE, campaign)
	doc.check_permission(ptype)

	return doc
