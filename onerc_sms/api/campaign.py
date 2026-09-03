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
from frappe.model import default_fields, no_value_fields, optional_fields

from onerc_sms.onerc_sms.doctype.sms_campaign.filters import operators_for

CAMPAIGN_DOCTYPE = "SMS Campaign"

#: Field types that can hold a phone number. `Phone` is the obvious one; `Data`
#: is here because most societies' contact fields predate it.
PHONE_FIELDTYPES = ("Data", "Phone")

#: How many of a campaign's recipients the preview shows. Enough to see that the
#: template rendered, not enough to be a way of exporting the audience.
PREVIEW_SAMPLE = 3

# Enough to make an ordinary branch/status/category field useful without
# turning the campaign builder into an unbounded export of a source doctype.
FILTER_VALUE_LIMIT = 100

#: What the columns every doctype has actually hold. `Meta.fields` never
#: mentions them, so there is nowhere else to read this from, and without it
#: "everybody enrolled since the flood" -- a filter on `creation` -- would be
#: offered Like and In rather than the date comparisons it needs.
_STANDARD_FIELDTYPES = {
	"creation": "Datetime",
	"modified": "Datetime",
	"owner": "Link",
	"modified_by": "Link",
	"docstatus": "Int",
	"parent": "Data",
	"parentfield": "Data",
	"parenttype": "Data",
	"name": "Data",
}


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
			# Carried so the grid can shape the rest of the row the moment a
			# field is picked, rather than after a second round trip.
			"fieldtype": field.fieldtype,
		}
		for field in meta.fields
		if field.fieldtype not in no_value_fields and field.fieldname
	]

	fields += [
		{
			"value": name,
			"label": name,
			"description": _("standard field"),
			"fieldtype": _STANDARD_FIELDTYPES.get(name, "Data"),
		}
		for name in default_fields
		if name not in ("doctype", "idx")
	]

	fields.sort(key=lambda field: field["label"].lower())

	return fields


@frappe.whitelist()
def get_filter_values(doctype: str, fieldname: str) -> dict:
	"""Suggestions for one filter field, under the caller's source permissions.

	Select fields return their configured vocabulary even when a choice has not
	yet appeared in a record. Every other value-bearing field returns the first
	100 distinct values the caller can actually read through ``frappe.get_list``.
	That last detail is the access control: onerc_core's geo query condition and
	the source doctype's ordinary read permission run exactly as they do when the
	campaign itself resolves its audience.
	"""
	frappe.has_permission(CAMPAIGN_DOCTYPE, ptype="read", throw=True)

	meta = frappe.get_meta(doctype)
	field = meta.get_field(fieldname)
	standard = fieldname in (set(default_fields) | set(optional_fields))

	if not field and not standard:
		frappe.throw(_("{0} is not a field on {1}.").format(frappe.bold(fieldname), frappe.bold(doctype)))

	if field and field.fieldtype in no_value_fields:
		frappe.throw(_("{0} does not hold a value that can be filtered.").format(frappe.bold(fieldname)))

	fieldtype = field.fieldtype if field else _STANDARD_FIELDTYPES.get(fieldname, "Data")

	if fieldtype == "Check":
		# Stored as 0/1, which is not what anybody would type. The grid shows the
		# words and sends the digit.
		choices = [{"value": "1", "label": _("Yes")}, {"value": "0", "label": _("No")}]
	elif fieldtype == "Select":
		choices = [
			{"value": choice.strip(), "label": choice.strip()}
			for choice in (field.options or "").split("\n")
			if choice.strip()
		]
	elif fieldtype == "Link" and field and field.options:
		choices = _linked_records(field.options)
	else:
		rows = frappe.get_list(
			doctype,
			fields=[fieldname],
			filters=[[doctype, fieldname, "is", "set"]],
			group_by=fieldname,
			order_by=f"{fieldname} asc",
			limit_page_length=FILTER_VALUE_LIMIT,
		)
		choices = [
			{"value": str(row.get(fieldname)), "label": str(row.get(fieldname))}
			for row in rows
			if row.get(fieldname) not in (None, "")
		]

	return {
		"fieldtype": fieldtype,
		# The shortlist this field can actually be asked about. Sent with the
		# values rather than worked out on the form, so the vocabulary the grid
		# offers and the one `filters.validate` accepts are the same list.
		"operators": operators_for(fieldtype),
		"values": choices,
		"truncated": len(choices) == FILTER_VALUE_LIMIT,
	}


def _linked_records(target: str) -> list[dict]:
	"""The records a Link field may point at, named the way a person reads them.

	**Read from the target doctype, not from the values already used.** Asking
	the source for its distinct `geo_node` values answers "which branches has
	somebody already been filed under", which is not the question: a campaign is
	very often the first thing addressed to a branch, and a picker that could
	only offer branches with existing records would have nothing to say on the
	day it mattered. This is also what the desk's own Link filter does.

	Labelled by the target's title field where it has one, because `GEO-00042`
	is not a branch anybody can pick from a list. `frappe.get_list` applies the
	target's own read permission, so a coordinator is offered the branches they
	may see and no others.
	"""
	try:
		meta = frappe.get_meta(target)
	except frappe.DoesNotExistError:
		return []

	title = meta.get_title_field()
	# `get_title_field` falls back to "name", and asking for it twice makes the
	# query invalid as well as pointless.
	fields = ["name"] + ([title] if title and title != "name" else [])

	try:
		rows = frappe.get_list(
			target,
			fields=fields,
			order_by=f"{title or 'name'} asc",
			limit_page_length=FILTER_VALUE_LIMIT,
		)
	except frappe.PermissionError:
		# Somebody who may build a campaign but may not read the target doctype
		# gets an empty picker rather than an error page; the field can still be
		# filtered by typing a value.
		return []

	choices = []

	for row in rows:
		name = row.get("name")
		label = row.get(title) if title else None
		choices.append(
			{"value": name, "label": f"{label} ({name})" if label and label != name else name}
		)

	return choices


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
