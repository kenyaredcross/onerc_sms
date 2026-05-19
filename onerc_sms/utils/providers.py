import frappe


def get_active_provider():
    provider = frappe.db.get_value(
        "SMS Provider",
        {"is_active": 1},
        ["name", "adapter", "username", "api_key", "sender_id"],
        as_dict=True
    )

    if not provider:
        frappe.throw("No active SMS provider found. Please configure one in SMS Provider.")

    return provider


def send_via_provider(provider, phone, message):
    if provider.adapter == "Africa's Talking":
        return send_via_africastalking(provider, phone, message)

    elif provider.adapter == "Twilio":
        return send_via_twilio(provider, phone, message)

    else:
        frappe.throw(f"Adapter {provider.adapter} is not yet implemented.")


def send_via_africastalking(provider, phone, message):
    import africastalking

    africastalking.initialize(provider.username, provider.api_key)
    sms = africastalking.SMS

    try:
        response = sms.send(message, [phone], provider.sender_id)
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])

        if not recipients:
            return {
                "status": "Failed",
                "status_code": None,
                "cost": 0.0,
                "message_id": None,
                "error": "No recipients in response"
            }

        r = recipients[0]
        cost_str = r.get("cost", "KES 0.00")

        try:
            cost = float(cost_str.split(" ")[1])
        except Exception:
            cost = 0.0

        return {
            "status": r.get("status"),
            "status_code": r.get("statusCode"),
            "cost": cost,
            "message_id": r.get("messageId"),
            "error": None
        }

    except Exception as e:
        return {
            "status": "Failed",
            "status_code": None,
            "cost": 0.0,
            "message_id": None,
            "error": str(e)
        }


def send_via_twilio(provider, phone, message):
    from twilio.rest import Client

    client = Client(provider.username, provider.api_key)

    try:
        msg = client.messages.create(
            body=message,
            from_=provider.sender_id,
            to=phone
        )

        return {
            "status": msg.status,
            "status_code": None,
            "cost": float(msg.price or 0.0) * -1,
            "message_id": msg.sid,
            "error": None
        }

    except Exception as e:
        return {
            "status": "Failed",
            "status_code": None,
            "cost": 0.0,
            "message_id": None,
            "error": str(e)
        }