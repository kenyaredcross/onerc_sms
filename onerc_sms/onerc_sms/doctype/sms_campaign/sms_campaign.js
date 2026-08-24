// Copyright (c) 2026, Kelvin Njenga and contributors
// For license information, please see license.txt

// The filter grid works the way the desk's own list filters do: pick the source
// doctype, and the Field column offers that doctype's fields and nothing else,
// shown by their form labels with the fieldname underneath. The options are
// fetched once per doctype from `onerc_sms.api.campaign.get_filter_fields` and
// pushed onto the grid's docfield, which is how Frappe itself drives a dynamic
// column (see erpnext/public/js/utils.js for the same call).

frappe.ui.form.on("SMS Campaign", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Scheduled") {
			frm.add_custom_button(__("Send Now"), () => {
				frappe.confirm(__("Are you sure you want to send this campaign now?"), () => {
					frappe.call({
						method: "onerc_sms.api.campaign.send_now",
						args: { campaign: frm.doc.name },
						callback: () => {
							frappe.msgprint(__("Campaign sent successfully."));
							frm.reload_doc();
						},
					});
				});
			});
		}

		frm.add_custom_button(__("Preview"), () => {
			if (frm.doc.__islocal || frm.is_dirty()) {
				frappe.msgprint(__("Please save the campaign first before previewing"));
				return;
			}

			frappe.call({
				method: "onerc_sms.api.campaign.preview_campaign",
				args: { campaign: frm.doc.name },
				callback: (r) => {
					if (!r.message) return;

					const data = r.message;
					let html = `<p><b>${__("Total recipients")}: ${data.total}</b></p><hr>`;

					data.preview.forEach((p) => {
						html += `<p><b>${frappe.utils.escape_html(p.phone)}</b><br>${frappe.utils.escape_html(
							p.message
						)}</p><hr>`;
					});

					frappe.msgprint({ title: __("Campaign Preview"), message: html, wide: true });
				},
			});
		});

		frm.trigger("load_filter_fields");
	},

	// A saved campaign opened fresh needs the picker populated too, and refresh
	// alone can run before the grid exists.
	onload_post_render(frm) {
		frm.trigger("load_filter_fields");
	},

	source_type(frm) {
		frm.trigger("load_filter_fields");
	},

	source_doctype(frm) {
		if (!frm.doc.source_doctype) return;

		// Filter rows name fields on the doctype that was chosen when they were
		// written. Kept across a change of doctype they would fail validation on
		// save with a message about a field that is not there — clearing them is
		// the same thing the person would have to do by hand, said out loud.
		if ((frm.doc.campaign_filters || []).length) {
			frm.clear_table("campaign_filters");
			frm.refresh_field("campaign_filters");
			frappe.show_alert({
				message: __("Filters cleared — they belonged to the previous doctype."),
				indicator: "orange",
			});
		}

		frm.trigger("load_filter_fields");
		frm.trigger("detect_phone_field");
	},

	load_filter_fields(frm) {
		const grid = frm.fields_dict.campaign_filters && frm.fields_dict.campaign_filters.grid;

		if (!grid || !frm.doc.source_doctype || frm.doc.source_type !== "Doctype Query") return;

		// Cached per doctype: the grid is refreshed on every form refresh and a
		// field list does not change between them.
		frm.__filter_fields = frm.__filter_fields || {};

		const apply = (options) => {
			try {
				grid.update_docfield_property("filter_field", "options", options);
				grid.refresh();
			} catch (e) {
				// The grid is not built yet. onload_post_render will come back.
				console.warn("SMS Campaign: filter field options not applied yet", e);
			}
		};

		if (frm.__filter_fields[frm.doc.source_doctype]) {
			apply(frm.__filter_fields[frm.doc.source_doctype]);
			return;
		}

		frappe.call({
			method: "onerc_sms.api.campaign.get_filter_fields",
			args: { doctype: frm.doc.source_doctype },
			callback: (r) => {
				if (!r.message) return;

				frm.__filter_fields[frm.doc.source_doctype] = r.message;
				apply(r.message);
			},
		});
	},

	load_filter_values(frm, row) {
		const grid = frm.fields_dict.campaign_filters && frm.fields_dict.campaign_filters.grid;

		if (!grid || !frm.doc.source_doctype || !row || !row.filter_field) return;

		frm.__filter_values = frm.__filter_values || {};
		const cache_key = `${frm.doc.source_doctype}:${row.filter_field}`;

		const apply = (result) => {
			grid.update_docfield_property("filter_value", "options", result.values || []);
			const grid_row = grid.grid_rows_by_docname && grid.grid_rows_by_docname[row.name];
			if (grid_row) grid_row.refresh_field("filter_value");
		};

		if (frm.__filter_values[cache_key]) {
			apply(frm.__filter_values[cache_key]);
			return;
		}

		frappe.call({
			method: "onerc_sms.api.campaign.get_filter_values",
			args: { doctype: frm.doc.source_doctype, fieldname: row.filter_field },
			callback: (r) => {
				if (!r.message) return;
				frm.__filter_values[cache_key] = r.message;
				apply(r.message);
			},
		});
	},

	detect_phone_field(frm) {
		if (!frm.doc.source_doctype) return;

		frappe.call({
			method: "onerc_sms.api.campaign.get_doctype_fields",
			args: { doctype: frm.doc.source_doctype },
			callback: (r) => {
				if (!r.message) return;

				const phone_fields = r.message.filter((f) =>
					["phone", "mobile", "telephone", "contact", "number"].some((keyword) =>
						f.toLowerCase().includes(keyword)
					)
				);

				if (phone_fields.length === 1) {
					frm.set_value("phone_field", phone_fields[0]);
					frappe.show_alert({
						message: __('Phone field automatically set to "{0}"', [phone_fields[0]]),
						indicator: "green",
					});
				} else if (phone_fields.length > 1) {
					frappe.show_alert({
						message: __("Multiple phone fields found: {0}. Please select one.", [
							phone_fields.join(", "),
						]),
						indicator: "orange",
					});
				} else {
					frappe.show_alert({
						message: __("No phone field detected automatically. Please type the field name."),
						indicator: "red",
					});
				}
			},
		});
	},

	template(frm) {
		if (!frm.doc.template) return;

		frappe.db.get_value("SMS Template", frm.doc.template, "message", (r) => {
			if (r && r.message) {
				frm.set_value("message", r.message);
			}
		});
	},
});

frappe.ui.form.on("SMS Campaign Filter", {
	// A row added before the options landed would render an empty picker.
	campaign_filters_add(frm) {
		frm.trigger("load_filter_fields");
	},

	filter_field(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "filter_value", "");
		frm.events.load_filter_values(frm, row);
	},

	form_render(frm, cdt, cdn) {
		frm.events.load_filter_values(frm, locals[cdt][cdn]);
	},
});
