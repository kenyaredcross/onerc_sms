frappe.ui.form.on("SMS Campaign", {

    refresh(frm) {
        if (frm.doc.docstatus === 1 && frm.doc.status === "Scheduled") {
            frm.add_custom_button("Send Now", () => {
                frappe.confirm(
                    "Are you sure you want to send this campaign now?",
                    () => {
                        frappe.call({
                            method: "onerc_sms.api.campaign.send_now",
                            args: { campaign: frm.doc.name },
                            callback: (r) => {
                                frappe.msgprint("Campaign sent successfully.");
                                frm.reload_doc();
                            }
                        });
                    }
                );
            });
        }; 
        frm.add_custom_button("Preview", () => {

            if (frm.doc.__islocal || frm.is_dirty()){
                frappe.msgprint("Please save the campaign first before previewing");
                return;
            }
            
            frappe.call({
                method: "onerc_sms.api.campaign.preview_campaign",
                args: { campaign: frm.doc.name },
                callback: (r) => {
                    if (!r.message) return;

                    let data = r.message;
                    let html = `<p><b>Total recipients: ${data.total}</b></p><hr>`;

                    data.preview.forEach(p => {
                        html += `<p><b>${p.phone}</b><br>${p.message}</p><hr>`;
                    });

                    frappe.msgprint({
                        title: "Campaign Preview",
                        message: html,
                        wide: true
                    });
                }
            });
        });
    },

    source_doctype(frm) {
        if (!frm.doc.source_doctype) return;

        frappe.call({
            method: "onerc_sms.api.campaign.get_doctype_fields",
            args: { doctype: frm.doc.source_doctype },
            callback: (r) => {
                if (!r.message) return;

                let all_fields = r.message;

                let phone_fields = all_fields.filter(f =>
                    ["phone", "mobile", "telephone", "contact", "number"]
                    .some(keyword => f.toLowerCase().includes(keyword))
                );

                if (phone_fields.length === 1) {
                    frm.set_value("phone_field", phone_fields[0]);
                    frappe.show_alert({
                        message: `Phone field automatically set to "${phone_fields[0]}"`,
                        indicator: "green"
                    });
                } else if (phone_fields.length > 1) {
                    frappe.show_alert({
                        message: `Multiple phone fields found: ${phone_fields.join(", ")}. Please select one.`,
                        indicator: "orange"
                    });
                } else {
                    frappe.show_alert({
                        message: "No phone field detected automatically. Please type the field name.",
                        indicator: "red"
                    });
                }
            }
        });
    },

    template(frm) {
        if (!frm.doc.template) return;

        frappe.db.get_value("SMS Template", frm.doc.template, "message", (r) => {
            if (r && r.message) {
                frm.set_value("message", r.message);
            }
        });
    }

});