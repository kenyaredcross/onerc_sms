# Copyright (c) 2026, Kelvin Njenga and contributors
# For license information, please see license.txt

"""Talking to an SMS gateway, and the two gateways this app ships configured.

Bringing a society online is one `SMS Provider` row: pick the adapter, paste the
two credentials the gateway issued, name the sender. Both SDKs are declared in
`pyproject.toml`, so `bench get-app` installs them and there is no second step
and nothing to `pip install` by hand.

**Every adapter answers in the same shape**, and the caller depends on nothing
else::

    {"status": "Success" | "Failed", "status_code", "cost", "message_id", "error"}

`status` is normalised here rather than passed through. The gateways disagree
about what success is called -- Africa's Talking says ``Success``, Twilio says
``queued`` -- and `SMSCampaign.send_sms` counts a send by comparing against one
word. Passing the raw string through is why a flawless Twilio campaign used to
finish with `total_failed` equal to its recipient count and a status of Failed.
The gateway's own word is kept on `status_code` and `error`, so nothing is lost.

**No adapter raises.** A per-recipient failure returns a Failed result and the
send loop carries on to the next number, which is the only sane behaviour for a
broadcast: one bad number must not cost the other eight hundred. Everything that
*would* be worth stopping for -- an unsupported adapter, a missing SDK, a blank
credential -- is asked once by `ensure_ready()` before the loop starts, while the
campaign is still untouched.

**Credentials are read through `get_password`.** `api_key` is a Password field,
so the value lives encrypted in `__Auth` rather than in the table. `get_password`
returns a plain in-memory value unchanged when it is not a masked placeholder,
which is what makes this work on a site whose key predates that change without
any migration having to have run first.
"""

import json
import re

import frappe
from frappe import _

#: The two words `SMSCampaign.send_sms` counts by. Anything not Success is
#: Failed; there is deliberately no third state, because a campaign row either
#: reached the gateway or it did not.
STATUS_SUCCESS = "Success"
STATUS_FAILED = "Failed"

ADAPTER_AFRICASTALKING = "Africa's Talking"
ADAPTER_TWILIO = "Twilio"

PROVIDER_DOCTYPE = "SMS Provider"
SETTINGS_DOCTYPE = "OneRC SMS Settings"

#: What each adapter calls the three credential fields, and whether a sender is
#: required. One table, read by the desk form (to relabel the fields for the
#: chosen gateway), by `ensure_ready` (to say precisely what is missing) and by
#: the setup guide in the README. Adding a third gateway is adding a row here
#: and a function below.
ADAPTERS = {
	ADAPTER_AFRICASTALKING: {
		"package": "africastalking",
		"username_label": "Username",
		"username_hint": "Your Africa's Talking username. Use <b>sandbox</b> to test against"
		" their free sandbox before going live.",
		"api_key_label": "API Key",
		"api_key_hint": "Africa's Talking dashboard &rarr; Settings &rarr; API Key.",
		"sender_id_label": "Sender ID / Short Code",
		"sender_id_hint": "The alphanumeric sender ID or short code registered with Africa's"
		" Talking, e.g. <b>REDCROSS</b>. Leave blank on sandbox.",
		"sender_required": False,
	},
	ADAPTER_TWILIO: {
		"package": "twilio",
		"username_label": "Account SID",
		"username_hint": "Twilio Console &rarr; Account Info &rarr; Account SID. Starts with"
		" <b>AC</b>.",
		"api_key_label": "Auth Token",
		"api_key_hint": "Twilio Console &rarr; Account Info &rarr; Auth Token.",
		"sender_id_label": "From Number / Messaging Service SID",
		"sender_id_hint": "Either a Twilio number in E.164 form, e.g. <b>+15017122661</b>, or a"
		" Messaging Service SID starting with <b>MG</b> -- this field accepts both.",
		"sender_required": True,
	},
}

#: Twilio message states that mean the gateway took the message. The rest --
#: failed, undelivered, canceled -- are counted as failures. Twilio reports
#: `queued` or `accepted` at creation time and only resolves to `delivered`
#: later, out of band, so treating anything short of `delivered` as a failure
#: would fail every message this app has ever sent.
TWILIO_ACCEPTED = frozenset({"accepted", "scheduled", "queued", "sending", "sent", "delivered"})

#: A Twilio sender beginning with these two characters is a Messaging Service,
#: which is passed under a different keyword from a plain number.
TWILIO_MESSAGING_SERVICE_PREFIX = "MG"


# --------------------------------------------------------------- the provider


def get_active_provider():
	"""The gateway this site sends through, as a Document.

	A Document rather than the dict this used to return, because the API key is
	a Password field now and `get_password` is a method on the document -- there
	is no way to read an encrypted value off a `frappe.db.get_value` row.

	`is_active` stays the authority. The fall back to OneRC SMS Settings'
	`default_provider` only covers the case that used to be a hard error -- no
	provider marked active at all -- so it can widen what works and never change
	which gateway an already-working site sends through.
	"""
	name = frappe.db.get_value(PROVIDER_DOCTYPE, {"is_active": 1}, "name")

	if not name:
		name = frappe.db.get_single_value(SETTINGS_DOCTYPE, "default_provider")

	if not name or not frappe.db.exists(PROVIDER_DOCTYPE, name):
		frappe.throw(
			_(
				"No active SMS provider. Open <b>SMS Provider</b>, fill in the credentials for"
				" your gateway and tick <b>Is Active</b>."
			),
			title=_("No SMS Provider"),
		)

	return frappe.get_doc(PROVIDER_DOCTYPE, name)


def resolve_credentials(provider) -> dict:
	"""The three credentials, decrypted, whitespace stripped.

	Stripped because a credential is almost always pasted, and a trailing space
	on an auth token produces a 401 that reads like a wrong password -- the
	single most expensive minute in setting one of these up.
	"""
	return {
		"username": (provider.username or "").strip(),
		"api_key": (provider.get_password("api_key", raise_exception=False) or "").strip(),
		"sender_id": (provider.sender_id or "").strip(),
	}


def ensure_ready(provider) -> None:
	"""Can this provider send at all? Asked once, before a campaign is touched.

	Everything here is a configuration mistake rather than a delivery failure,
	and each one is worth stopping the whole campaign for: they would otherwise
	produce a delivery log with one identical error on every row.

	Raised as a `frappe.ValidationError` with the fix in the message, because
	the person who sees it is an administrator on the desk, not a developer with
	the traceback.
	"""
	adapter = (provider.adapter or "").strip()
	spec = ADAPTERS.get(adapter)

	if not spec:
		frappe.throw(
			_("<b>{0}</b> has no adapter selected, or one this app cannot send through. Choose {1}.").format(
				provider.name, " or ".join(f"<b>{name}</b>" for name in ADAPTERS)
			),
			title=_("Adapter Not Supported"),
		)

	if not _package_available(spec["package"]):
		frappe.throw(
			_(
				"The <b>{0}</b> Python package is not installed on this server, so {1} cannot"
				" send. Run <code>bench pip install {0}</code> and restart, or reinstall the app"
				" so its declared dependencies are picked up."
			).format(spec["package"], adapter),
			title=_("Gateway Library Missing"),
		)

	credentials = resolve_credentials(provider)
	missing = [
		spec["username_label"] if not credentials["username"] else None,
		spec["api_key_label"] if not credentials["api_key"] else None,
		spec["sender_id_label"] if spec["sender_required"] and not credentials["sender_id"] else None,
	]
	missing = [label for label in missing if label]

	if missing:
		frappe.throw(
			_("<b>{0}</b> is missing {1}. Fill it in on the SMS Provider record.").format(
				provider.name, ", ".join(f"<b>{label}</b>" for label in missing)
			),
			title=_("Credentials Incomplete"),
		)


def _package_available(package: str) -> bool:
	"""Is the SDK importable, without paying to import it?

	`find_spec` rather than a try/import, so asking the question on a form
	refresh does not drag several megabytes of HTTP client into the worker.
	"""
	from importlib.util import find_spec

	try:
		return find_spec(package) is not None
	except (ImportError, ValueError):
		return False


# ------------------------------------------------------------------ dispatch


def send_via_provider(provider, phone: str, message: str) -> dict:
	"""Hand one message to the gateway. Never raises -- see the module docstring."""
	credentials = resolve_credentials(provider)
	adapter = (provider.adapter or "").strip()

	if adapter == ADAPTER_AFRICASTALKING:
		return _send_via_africastalking(credentials, phone, message)

	if adapter == ADAPTER_TWILIO:
		return _send_via_twilio(credentials, phone, message)

	# Unreachable when `ensure_ready` ran first, which every send path does.
	# Kept as a result rather than a throw so that a caller which skipped it
	# still logs a row instead of aborting mid-campaign.
	return _failed(f"Adapter {adapter or '(none)'} is not supported.")


def _result(status, status_code=None, cost=0.0, message_id=None, error=None) -> dict:
	return {
		"status": status,
		"status_code": status_code,
		"cost": cost,
		"message_id": message_id,
		"error": error,
	}


def _failed(error, status_code=None) -> dict:
	return _result(STATUS_FAILED, status_code=status_code, error=str(error)[:500])


# ---------------------------------------------------------- Africa's Talking


def _send_via_africastalking(credentials: dict, phone: str, message: str) -> dict:
	"""One message through Africa's Talking.

	Built on `SMSService` directly rather than `africastalking.initialize()`,
	which assigns eight service objects onto module globals. A Frappe worker is
	long-lived and serves every site in the bench, so a module global holding
	one society's API key is a credential leak waiting for the next request;
	an instance built per call cannot be read by anybody else.

	Sandbox comes free: the SDK routes to the sandbox host whenever the username
	is literally ``sandbox``, so a society can prove the wiring works before
	buying credit.
	"""
	try:
		from africastalking.SMS import SMSService
	except ImportError as exception:
		return _failed(f"africastalking is not installed: {exception}")

	try:
		service = SMSService(credentials["username"], credentials["api_key"])
		# `sender_id or None` and not `sender_id`: the SDK sends the `from`
		# parameter whenever it is not None, and an empty `from` is rejected by
		# the gateway -- where omitting it entirely is valid and is what sandbox
		# accounts need.
		response = service.send(message, [phone], credentials["sender_id"] or None)
	except Exception as exception:
		# Covers the SDK's own ValueError for a number its regex rejects, its
		# AfricasTalkingException for any non-2xx, and requests' timeouts.
		return _failed(exception)

	return _read_africastalking_response(response)


def _read_africastalking_response(response) -> dict:
	"""Turn whatever the gateway said into the canonical result shape.

	The SDK only parses JSON when the response's content type is exactly
	`application/json`, and hands back the raw body as a string otherwise -- so
	the string case is a real one and not defensive padding.
	"""
	data = response

	if isinstance(data, str | bytes):
		try:
			data = json.loads(data)
		except (ValueError, TypeError):
			return _failed(f"Unreadable response from Africa's Talking: {str(response)[:200]}")

	if not isinstance(data, dict):
		return _failed(f"Unexpected response from Africa's Talking: {str(response)[:200]}")

	envelope = data.get("SMSMessageData") or {}
	recipients = envelope.get("Recipients") or []

	if not recipients:
		# The envelope's own Message says why -- "Invalid sender id", "Insufficient
		# balance" -- and is far more use than "no recipients in response".
		return _failed(envelope.get("Message") or "Africa's Talking accepted no recipients.")

	recipient = recipients[0]
	status = (recipient.get("status") or "").strip()

	return _result(
		status=STATUS_SUCCESS if status.lower() == "success" else STATUS_FAILED,
		status_code=recipient.get("statusCode"),
		cost=_parse_cost(recipient.get("cost")),
		message_id=recipient.get("messageId"),
		error=None if status.lower() == "success" else status or "Rejected by Africa's Talking",
	)


def _parse_cost(value) -> float:
	"""The number out of a string like ``KES 0.8000``.

	By search rather than by splitting on a space: the currency prefix is not
	guaranteed, sandbox returns a bare ``0``, and a split that assumed two tokens
	is what used to silently report every message as free.
	"""
	match = re.search(r"[-+]?\d*\.?\d+", str(value or ""))

	if not match:
		return 0.0

	try:
		return abs(float(match.group()))
	except ValueError:
		return 0.0


# ----------------------------------------------------------------- Twilio


def _send_via_twilio(credentials: dict, phone: str, message: str) -> dict:
	"""One message through Twilio.

	The sender field accepts both of the things Twilio calls a sender: a
	Messaging Service SID goes under `messaging_service_sid` and anything else
	under `from_`. They are told apart by the `MG` prefix, which is Twilio's own
	resource-ID convention, so an operator pastes whichever their account uses
	and nothing else on the form changes.
	"""
	try:
		from twilio.base.exceptions import TwilioRestException
		from twilio.rest import Client
	except ImportError as exception:
		return _failed(f"twilio is not installed: {exception}")

	sender = credentials["sender_id"]
	arguments = {"body": message, "to": phone}

	if sender.upper().startswith(TWILIO_MESSAGING_SERVICE_PREFIX):
		arguments["messaging_service_sid"] = sender
	else:
		arguments["from_"] = sender

	try:
		client = Client(credentials["username"], credentials["api_key"])
		sent = client.messages.create(**arguments)
	except TwilioRestException as exception:
		# Twilio's own error code is worth keeping: 21608 is "unverified number
		# on a trial account", 21211 "invalid To number", and an operator can
		# look either up.
		return _failed(exception.msg or str(exception), status_code=exception.code)
	except Exception as exception:
		return _failed(exception)

	status = (sent.status or "").strip().lower()

	return _result(
		status=STATUS_SUCCESS if status in TWILIO_ACCEPTED else STATUS_FAILED,
		# Twilio's own word is not an integer code, and the delivery log's
		# status_code column is an Int -- the word itself is kept in `error`
		# when it is a failure, which is the only time it tells you anything.
		status_code=None,
		# Twilio reports price as a negative number, and as None until the
		# message is billed -- which is always the case at creation time. A
		# campaign's total cost is therefore 0.0 on Twilio unless a status
		# callback fills it in later; that is Twilio's model, not a parse bug.
		cost=_parse_cost(sent.price),
		message_id=sent.sid,
		error=None if status in TWILIO_ACCEPTED else (sent.error_message or status),
	)
