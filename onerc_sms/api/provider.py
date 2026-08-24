# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""What the SMS Provider form needs from the server.

Three questions, and the answer to all of them lives in
`onerc_sms.utils.providers` rather than here or in the client script: what does
this gateway call its credentials, is this record able to send right now, and
does it actually work. Duplicating any of that into JavaScript would be a second
place to update when a third gateway is added, and the one most likely to be
missed.

**Every endpoint checks permission on the record.** These read decrypted
credentials and spend real money, so `frappe.get_doc` -- which performs no
permission check of its own -- is never the last word here.
"""

import frappe
from frappe import _

from onerc_sms.utils.providers import (
	ADAPTERS,
	STATUS_SUCCESS,
	ensure_ready,
	get_active_provider,
	resolve_credentials,
	send_via_provider,
)

PROVIDER_DOCTYPE = "SMS Provider"


@frappe.whitelist()
def describe(adapter: str | None = None) -> dict:
	"""What this gateway calls its three credential fields.

	Read by the form to relabel `username`, `api_key` and `sender_id` the moment
	an adapter is chosen, so an operator is asked for an "Account SID" rather
	than being left to guess that Twilio's SID goes in a box marked Username.

	Whitelisted with only a read check on the doctype: it returns field labels
	and help text, never a credential and never anything about a specific record.
	"""
	frappe.has_permission(PROVIDER_DOCTYPE, ptype="read", throw=True)

	if adapter:
		return {"adapter": adapter, **ADAPTERS.get(adapter, {})}

	return {"adapters": ADAPTERS}


@frappe.whitelist()
def readiness(provider: str) -> dict:
	"""Could this record send a message right now, and if not, what is missing?

	The same `ensure_ready` a campaign runs before it dispatches, asked from the
	form so the answer arrives while somebody is looking at the fields rather
	than when four hundred messages were supposed to go out.
	"""
	doc = _get_checked(provider, "read")

	try:
		ensure_ready(doc)
	except frappe.ValidationError:
		# `ensure_ready` throws through frappe.throw, which files the message on
		# the response and would surface as a modal on top of the form. The form
		# renders it inline instead, so the message is taken and the queue
		# cleared.
		message = frappe.message_log[-1].get("message") if frappe.message_log else None
		frappe.clear_messages()

		return {"ready": False, "message": message or _("This provider cannot send yet.")}

	return {
		"ready": True,
		"message": _("Ready to send through {0} as {1}.").format(
			frappe.bold(doc.adapter),
			frappe.bold(resolve_credentials(doc)["sender_id"] or _("the gateway default sender")),
		),
	}


@frappe.whitelist()
def send_test(provider: str, phone: str) -> dict:
	"""Send one real message, to prove the credentials before a campaign uses them.

	Deliberately a real send and not a dry run: the failures worth catching
	before going live -- a rejected sender ID, an unverified number on a Twilio
	trial, an empty account balance -- are all things only the gateway knows,
	and none of them show up in a simulated call.

	Gated on `write`, not `read`. It spends the society's credit, so it belongs
	to whoever may change the record rather than to anybody who may look at it.
	"""
	doc = _get_checked(provider, "write")

	phone = (phone or "").strip().replace(" ", "")

	if not phone.startswith("+"):
		frappe.throw(
			_("Enter the number in international form, starting with + and the country code."),
			frappe.ValidationError,
			title=_("Country Code Needed"),
		)

	# Throws with the fix in the message if the gateway is not configured, which
	# is the far more common reason a test fails than the message itself.
	ensure_ready(doc)

	result = send_via_provider(doc, phone, _("Test message from {0}. SMS is configured correctly.").format(
		frappe.db.get_single_value("Website Settings", "app_name") or "OneRC"
	))

	return {
		"sent": result["status"] == STATUS_SUCCESS,
		"phone": phone,
		"status": result["status"],
		"status_code": result["status_code"],
		"message_id": result["message_id"],
		"cost": result["cost"],
		"error": result["error"],
	}


@frappe.whitelist()
def active() -> dict:
	"""Which provider this site would send through, for anything that wants to say so."""
	frappe.has_permission(PROVIDER_DOCTYPE, ptype="read", throw=True)

	doc = get_active_provider()

	return {"provider": doc.name, "adapter": doc.adapter}


def _get_checked(provider: str, ptype: str):
	"""Load the record and insist the caller may do this to it.

	`frappe.get_doc` performs no permission check unless it is asked to, so the
	check is made explicitly rather than assumed -- these endpoints decrypt an
	API key and can spend money.
	"""
	doc = frappe.get_doc(PROVIDER_DOCTYPE, provider)
	doc.check_permission(ptype)

	return doc
