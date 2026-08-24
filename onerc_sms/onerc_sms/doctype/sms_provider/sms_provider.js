// Copyright (c) 2026, Kelvin Njenga and contributors
// For license information, please see license.txt

// The three credential fields serve both gateways, so the form asks for them in
// the words the chosen gateway uses: Twilio calls its username an Account SID
// and its API key an Auth Token, and somebody holding a Twilio console should
// not have to work out which box each one goes in. The labels and the help text
// come from `onerc_sms.api.provider.describe`, which reads the same ADAPTERS
// table the server validates against — there is no copy of it here.

frappe.ui.form.on("SMS Provider", {
	refresh(frm) {
		frm.trigger("apply_adapter_labels");
		frm.trigger("show_readiness");

		if (!frm.is_new()) {
			frm.add_custom_button(__("Send Test SMS"), () => frm.trigger("send_test"));
		}
	},

	adapter(frm) {
		frm.trigger("apply_adapter_labels");
		// The readiness line is about the saved record, and the adapter just
		// changed in the browser only. Saying nothing is more honest than
		// reporting on credentials that are no longer the ones on screen.
		frm.get_field("readiness")?.$wrapper.empty();
	},

	apply_adapter_labels(frm) {
		if (!frm.doc.adapter) return;

		frappe.call({
			method: "onerc_sms.api.provider.describe",
			args: { adapter: frm.doc.adapter },
			callback: (r) => {
				const spec = r.message;
				if (!spec || !spec.username_label) return;

				const fields = {
					username: ["username_label", "username_hint"],
					api_key: ["api_key_label", "api_key_hint"],
					sender_id: ["sender_id_label", "sender_id_hint"],
				};

				for (const [fieldname, [label, hint]] of Object.entries(fields)) {
					frm.set_df_property(fieldname, "label", __(spec[label]));
					frm.set_df_property(fieldname, "description", spec[hint]);
				}

				// Africa's Talking delivers on a shared sender when this is
				// blank, which is what a sandbox account needs; Twilio has
				// nowhere to send from without one.
				frm.set_df_property("sender_id", "reqd", spec.sender_required ? 1 : 0);
				frm.refresh_fields(["username", "api_key", "sender_id"]);
			},
		});
	},

	show_readiness(frm) {
		const field = frm.get_field("readiness");
		if (!field) return;

		field.$wrapper.empty();

		if (frm.is_new() || frm.is_dirty()) return;

		frappe.call({
			method: "onerc_sms.api.provider.readiness",
			args: { provider: frm.doc.name },
			callback: (r) => {
				if (!r.message) return;

				const { ready, message } = r.message;
				const indicator = ready ? "green" : "orange";

				field.$wrapper.html(
					`<div class="form-message ${ready ? "green" : "orange"}">
						<span class="indicator ${indicator}">${message}</span>
					</div>`
				);
			},
		});
	},

	send_test(frm) {
		// A real message to a real handset, because the failures worth finding
		// before a campaign — a rejected sender ID, an unverified number on a
		// trial account, no balance — are all things only the gateway knows.
		frappe.prompt(
			[
				{
					fieldname: "phone",
					fieldtype: "Data",
					label: __("Send a test message to"),
					reqd: 1,
					description: __(
						"International form, starting with + and the country code, e.g. +255712345678."
					),
				},
			],
			(values) => {
				frappe.dom.freeze(__("Sending…"));

				frappe.call({
					method: "onerc_sms.api.provider.send_test",
					args: { provider: frm.doc.name, phone: values.phone },
					always: () => frappe.dom.unfreeze(),
					callback: (r) => {
						if (!r.message) return;

						const result = r.message;

						if (result.sent) {
							frappe.msgprint({
								title: __("Test Message Sent"),
								indicator: "green",
								message: __(
									"{0} accepted a message for {1}.<br>Gateway reference: {2}",
									[frm.doc.adapter, result.phone, result.message_id || "—"]
								),
							});
							return;
						}

						frappe.msgprint({
							title: __("Test Message Refused"),
							indicator: "red",
							message: __("{0} refused the message:<br><b>{1}</b>", [
								frm.doc.adapter,
								frappe.utils.escape_html(result.error || __("no reason given")),
							]),
						});
					},
				});
			},
			__("Test This Gateway"),
			__("Send")
		);
	},
});
