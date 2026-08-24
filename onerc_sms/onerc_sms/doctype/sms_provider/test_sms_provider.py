# Copyright (c) 2026, Kelvin Njenga and Contributors
# See license.txt

"""What a gateway record must have before a campaign is allowed to trust it.

`ensure_ready` is the whole reason a misconfigured provider fails on the form
rather than halfway through a broadcast, so each thing it refuses is pinned
here, along with the promise that an API key never sits in the table in plain
text.
"""

import frappe
from frappe.tests import IntegrationTestCase

from onerc_sms.utils import providers

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class ProviderFixture(IntegrationTestCase):
	"""A provider record per test, torn down after it."""

	def make(self, **overrides):
		values = {
			"doctype": "SMS Provider",
			"provider_name": frappe.generate_hash(length=10),
			"adapter": providers.ADAPTER_AFRICASTALKING,
			"username": "sandbox",
			"api_key": "atsk_test_key",
			"sender_id": "REDCROSS",
			"is_active": 0,
		}
		values.update(overrides)

		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("SMS Provider", doc.name, force=True, ignore_permissions=True))

		return doc


class TestTheApiKeyIsNotStoredInTheClear(ProviderFixture):
	def test_the_column_holds_a_mask_and_the_value_still_reads_back(self):
		doc = self.make(api_key="a-real-looking-secret")

		self.assertEqual(
			set(frappe.db.get_value("SMS Provider", doc.name, "api_key")),
			{"*"},
			"the api_key column should hold only a mask",
		)
		self.assertEqual(
			providers.resolve_credentials(frappe.get_doc("SMS Provider", doc.name))["api_key"],
			"a-real-looking-secret",
		)

	def test_a_pasted_credential_is_stripped(self):
		# A trailing space on an auth token produces a 401 that reads exactly
		# like a wrong password, which is the most expensive minute in setting
		# one of these up.
		doc = self.make(api_key="  spaced-key  ", username="  sandbox  ")
		credentials = providers.resolve_credentials(frappe.get_doc("SMS Provider", doc.name))

		self.assertEqual(credentials["api_key"], "spaced-key")
		self.assertEqual(credentials["username"], "sandbox")


class TestOnlyOneProviderIsActive(ProviderFixture):
	def test_activating_one_deactivates_the_rest(self):
		first = self.make(is_active=1)
		second = self.make(is_active=1)

		self.assertEqual(frappe.db.get_value("SMS Provider", first.name, "is_active"), 0)
		self.assertEqual(frappe.db.get_value("SMS Provider", second.name, "is_active"), 1)


class TestAProviderIsRefusedBeforeACampaignTrustsIt(ProviderFixture):
	def test_a_complete_africastalking_record_is_ready(self):
		providers.ensure_ready(self.make())

	def test_a_complete_twilio_record_is_ready(self):
		providers.ensure_ready(
			self.make(adapter=providers.ADAPTER_TWILIO, username="ACxxxx", sender_id="+15017122661")
		)

	def test_an_adapter_with_nothing_behind_it_is_refused_on_save(self):
		with self.assertRaises(frappe.ValidationError):
			self.make(adapter="Custom")

	def test_twilio_without_a_sender_is_refused_on_save(self):
		# Twilio has nowhere to send from without one.
		with self.assertRaises(frappe.ValidationError):
			self.make(adapter=providers.ADAPTER_TWILIO, username="ACxxxx", sender_id="")

	def test_africastalking_without_a_sender_is_allowed(self):
		# It delivers on a shared sender, which is what a sandbox account needs.
		providers.ensure_ready(self.make(sender_id=""))

	def test_a_missing_credential_names_the_field_the_gateway_calls_it(self):
		doc = self.make(adapter=providers.ADAPTER_TWILIO, username="ACxxxx", sender_id="+15017122661")
		doc.db_set("username", "")
		doc.reload()

		with self.assertRaises(frappe.ValidationError):
			providers.ensure_ready(doc)

		# Twilio's word, not Frappe's fieldname: somebody holding a Twilio
		# console should not have to work out that username means Account SID.
		self.assertIn("Account SID", frappe.message_log[-1].get("message", ""))
		frappe.clear_messages()


class TestBothGatewaysAreInstalled(IntegrationTestCase):
	def test_the_sdks_are_importable(self):
		"""The failure an operator cannot diagnose from the desk.

		Both are declared in pyproject.toml so `bench get-app` installs them;
		this is what notices if that declaration is ever dropped.
		"""
		for adapter, spec in providers.ADAPTERS.items():
			with self.subTest(adapter=adapter):
				self.assertTrue(
					providers._package_available(spec["package"]),
					f"{spec['package']} is not installed, so {adapter} cannot send",
				)


class TestGatewayRepliesBecomeOneShape(IntegrationTestCase):
	"""The adapters normalise, so `send_sms` can count by one word."""

	def test_africastalking_success_is_read_as_success(self):
		result = providers._read_africastalking_response(
			{
				"SMSMessageData": {
					"Message": "Sent to 1/1 Total Cost: KES 0.8000",
					"Recipients": [
						{
							"statusCode": 101,
							"number": "+254711111111",
							"status": "Success",
							"cost": "KES 0.8000",
							"messageId": "ATXid_abc",
						}
					],
				}
			}
		)

		self.assertEqual(result["status"], providers.STATUS_SUCCESS)
		self.assertEqual(result["cost"], 0.8)
		self.assertEqual(result["message_id"], "ATXid_abc")
		self.assertIsNone(result["error"])

	def test_a_rejected_number_is_read_as_failed(self):
		result = providers._read_africastalking_response(
			{"SMSMessageData": {"Recipients": [{"statusCode": 403, "status": "UserInBlacklist"}]}}
		)

		self.assertEqual(result["status"], providers.STATUS_FAILED)
		self.assertEqual(result["status_code"], 403)
		self.assertEqual(result["error"], "UserInBlacklist")

	def test_an_empty_recipient_list_reports_the_gateway_s_own_reason(self):
		# "Invalid sender id" is worth far more to an operator than "no
		# recipients in response".
		result = providers._read_africastalking_response(
			{"SMSMessageData": {"Message": "Invalid sender id", "Recipients": []}}
		)

		self.assertEqual(result["status"], providers.STATUS_FAILED)
		self.assertEqual(result["error"], "Invalid sender id")

	def test_a_response_that_arrived_as_text_is_still_read(self):
		# The SDK only parses JSON when the content type is exactly
		# application/json, and hands back the raw body otherwise.
		result = providers._read_africastalking_response(
			'{"SMSMessageData": {"Recipients": [{"status": "Success", "cost": "KES 0.80"}]}}'
		)

		self.assertEqual(result["status"], providers.STATUS_SUCCESS)

	def test_an_unreadable_response_fails_rather_than_raising(self):
		result = providers._read_africastalking_response("<html>502 Bad Gateway</html>")

		self.assertEqual(result["status"], providers.STATUS_FAILED)
		self.assertIn("502", result["error"])

	def test_cost_survives_the_shapes_a_gateway_reports_it_in(self):
		self.assertEqual(providers._parse_cost("KES 0.8000"), 0.8)
		self.assertEqual(providers._parse_cost("0"), 0.0)
		self.assertEqual(providers._parse_cost(None), 0.0)
		self.assertEqual(providers._parse_cost("NGN 2.50"), 2.5)
		# Twilio reports price as a negative number.
		self.assertEqual(providers._parse_cost("-0.0075"), 0.0075)
		self.assertEqual(providers._parse_cost("free"), 0.0)
