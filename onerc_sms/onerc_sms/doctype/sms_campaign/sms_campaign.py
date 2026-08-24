import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from onerc_sms.onerc_sms.doctype.sms_campaign import filters as campaign_filters
from onerc_sms.utils.providers import (
    STATUS_SUCCESS,
    ensure_ready,
    get_active_provider,
    send_via_provider,
)


class SMSCampaign(Document):
	# pass

    def validate(self):
        self.validate_message()
        self.validate_source()
        self.validate_filters()

    def validate_filters(self):
        # Caught while the form is open, not at send time: a filter naming a
        # field that is not on the source doctype is a typo somebody can fix in
        # a second now, and a half-sent campaign with a raw SQL error in the log
        # later.
        if self.source_type != "Doctype Query":
            return

        campaign_filters.validate(self.source_doctype, self.campaign_filters)

    def on_submit(self):
        # Datetime fields submitted by the Desk arrive from JSON as strings.
        # Normalise before comparing; Python cannot order that wire value
        # against the datetime returned by now_datetime().
        if get_datetime(self.scheduled_at) <= now_datetime():
            self.send_campaign()
        else:
            self.db_set("status", "Scheduled")

    def validate_message(self):
        if not self.template and not self.message:
            frappe.throw("Please provide a message or select a template.")

        if self.template and not self.message:
            template = frappe.get_doc("SMS Template", self.template)
            self.message = template.message

    def validate_source(self):
        if self.source_type == "Doctype Query" and not self.source_doctype:
            frappe.throw("Please select a Source Doctype.")

        if self.source_type == "Doctype Query" and not self.phone_field:
            frappe.throw("Please select a Phone Field.")

        if self.source_type == "CSV Upload" and not self.csv_file:
            frappe.throw("Please attach a CSV file.")

        if self.source_type == "Manual" and not self.phone_numbers:
            frappe.throw("Please enter phone numbers.")

    def send_campaign(self):
        # Asked before anything is written. A missing credential or an
        # uninstalled gateway library would otherwise leave the campaign stuck
        # at Sending with a delivery log full of one identical error — and
        # `ensure_ready` says exactly which field to fill in.
        provider = get_active_provider()
        ensure_ready(provider)

        self.db_set("status", "Sending")

        recipients = self.resolve_contacts()
        recipients = self.run_pipeline(recipients)

        if not recipients:
            self.db_set("status", "Failed")
            frappe.throw("No valid recipients found after filtering.")

        self.send_sms(recipients, provider)

    def resolve_contacts(self):
        if self.source_type == "Doctype Query":
            return self.resolve_from_doctype()

        elif self.source_type == "CSV Upload":
            return self.resolve_from_csv()

        elif self.source_type == "Manual":
            return self.resolve_from_manual()

    def resolve_from_doctype(self):
        if not self.source_doctype or not self.phone_field:
            return []

        filters = self.build_filters()
        target_field, link_field = self._split_phone_field()

        # get_list, not get_all: get_all always ignores permissions, which
        # skipped onerc_core's geo-scoping hook entirely and let a campaign
        # against e.g. VMMS Volunteer resolve nationwide regardless of who
        # built it. Resolved as the campaign's owner rather than
        # frappe.session.user, because a scheduled campaign is dispatched by
        # process_scheduled_campaigns() — a cron job with no coordinator in
        # session — and it must still reach only the area its creator holds.
        records = frappe.get_list(
            self.source_doctype,
            filters=filters,
            fields=["*"],
            user=self.owner,
        )

        linked_phones = self._resolve_linked_phones(records, link_field, target_field) if link_field else {}

        recipients = []

        for record in records:
            if link_field:
                phone = linked_phones.get(record.get(link_field), "")
            else:
                phone = record.get(target_field, "")

            if not phone:
                continue

            phone = str(phone).strip()

            if not phone.startswith("+"):
                frappe.log_error(
                    f"Skipping {phone} in {self.source_doctype} — missing country code",
                    "SMS Campaign"
                )
                continue

            recipients.append({
                "phone": phone,
                "context": record
            })

        return recipients

    def _split_phone_field(self):
        """('phone', None) for a plain field on source_doctype, or
        ('phone', 'red_profile') for a dotted phone_field naming a Link field
        on source_doctype and the field to read on the other end of it.

        Volunteers and members carry no phone field of their own — it lives on
        the linked Red Profile — so a dotted phone_field is how this campaign
        reaches it live rather than trusting a mirrored copy.
        """
        if "." not in self.phone_field:
            return self.phone_field, None

        link_field, target_field = self.phone_field.split(".", 1)
        field = frappe.get_meta(self.source_doctype).get_field(link_field)

        if not field or field.fieldtype != "Link":
            frappe.throw(f"{link_field} is not a Link field on {self.source_doctype}.")

        return target_field, link_field

    def _resolve_linked_phones(self, records, link_field, target_field):
        """One phone per distinct linked row, fetched once rather than once
        per recipient.

        Read with ignore_permissions=True — the same argument
        vmmsx.notifications.services.audience makes for its own reads off Red
        Profile: geo scoping already ran on `records` above, so this only
        projects one more field off rows already inside it and grants no one
        access to a row they could not already reach.
        """
        link_doctype = frappe.get_meta(self.source_doctype).get_field(link_field).options
        names = sorted({record.get(link_field) for record in records if record.get(link_field)})

        if not names:
            return {}

        rows = frappe.get_all(
            link_doctype,
            filters={"name": ["in", names]},
            fields=["name", target_field],
            ignore_permissions=True,
        )

        return {row.name: row.get(target_field) for row in rows}

    def build_filters(self):
        """The campaign's filter rows, as a Frappe query filter.

        A list of triples rather than a dict — see `filters.py` for why the dict
        this used to build silently dropped every filter row but the last one on
        any given field.
        """
        return campaign_filters.build(self.campaign_filters)

    def resolve_from_csv(self):
        import csv
        import io

        if not self.csv_file:
            return []

        file_doc = frappe.get_doc("File", {"file_url": self.csv_file})
        file_content = file_doc.get_content()

        if isinstance(file_content, bytes):
            file_content = file_content.decode("utf-8")

        reader = csv.DictReader(io.StringIO(file_content))

        if "phone_number" not in reader.fieldnames:
            frappe.throw("CSV must have a column named phone_number")

        recipients = []

        for row in reader:
            phone = row.get("phone_number", "").strip()

            if not phone:
                continue

            if not phone.startswith("+"):
                frappe.log_error(
                    f"Skipping {phone} — missing country code",
                    "SMS Campaign"
                )
                continue

            context = {k: v for k, v in row.items() if k != "phone_number"}

            recipients.append({
                "phone": phone,
                "context": context
            })

        return recipients

    def resolve_from_manual(self):
        if not self.phone_numbers:
            return []

        numbers = [
            n.strip()
            for n in self.phone_numbers.splitlines()
            if n.strip()
        ]

        recipients = []

        for number in numbers:
            if not number.startswith("+"):
                frappe.log_error(
                    f"Skipping {number} — missing country code",
                    "SMS Campaign"
                )
                continue

            recipients.append({
                "phone": number,
                "context": {}
            })

        return recipients

    def run_pipeline(self, recipients):
        recipients = self.filter_opted_out(recipients)
        recipients = self.deduplicate(recipients)
        recipients = self.render_messages(recipients)
        return recipients

    def filter_opted_out(self, recipients):
        opted_out = frappe.db.get_all(
            "SMS Opt Out",
            fields=["phone_number"]
        )

        opted_out_numbers = {row.phone_number for row in opted_out}

        return [
            r for r in recipients
            if r["phone"] not in opted_out_numbers
        ]

    def deduplicate(self, recipients):
        seen = set()
        unique = []

        for r in recipients:
            if r["phone"] not in seen:
                seen.add(r["phone"])
                unique.append(r)

        return unique

    def render_messages(self, recipients):
        rendered = []


        for r in recipients:
            try:
                message = frappe.render_template(self.message, r["context"])
                rendered.append({
                    "phone": r["phone"],
                    "context": r["context"],
                    "message": message
                })
            except Exception as e:
                frappe.log_error(
                    f"Failed to render message for {r['phone']}: {str(e)}",
                    "SMS Campaign"
                )
                continue

        return rendered

    def send_sms(self, recipients, provider=None):
        # The provider is passed in by `send_campaign`, which already ran
        # `ensure_ready` against it. Resolved here only for a caller that went
        # straight to this method.
        if provider is None:
            provider = get_active_provider()
            ensure_ready(provider)

        total_sent = 0
        total_failed = 0
        total_cost = 0.0

        self.db_set("total_recipients", len(recipients))

        for recipient in recipients:
            result = send_via_provider(
                provider,
                recipient["phone"],
                recipient["message"]
            )

            if result["status"] == STATUS_SUCCESS:
                total_sent += 1
            else:
                total_failed += 1

            total_cost += result["cost"]

            log_entry = frappe.new_doc("SMS Campaign Log")
            log_entry.update({
                "parent": self.name,
                "parenttype": self.doctype,
                "parentfield": "delivery_log",
                "phone_number": recipient["phone"],
                "status": result["status"],
                "status_code": result["status_code"],
                "cost": result["cost"],
                "message_id": result["message_id"],
                "error": result["error"],
                "timestamp": now_datetime()
            })
            log_entry.insert(ignore_permissions=True)

        self.db_set("total_sent", total_sent)
        self.db_set("total_failed", total_failed)
        self.db_set("total_cost", total_cost)
        self.db_set("status", "Sent" if total_failed == 0 else "Failed")
