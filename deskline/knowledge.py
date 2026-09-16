"""Human-authored facts about a fictional business. No LLM or RAG in this MVP."""

BUSINESS = "Brightside Device Repair"
MENU = (
    "For hours, press 1. For the diagnostic fee, press 2. For services, press 3. "
    "To end the call, press 0."
)
GREETING = (
    "Welcome to Deskline, a demo phone service for fictional Brightside Device Repair. "
)
POLICIES = {
    "1": {
        "title": "Business hours",
        "source": "hours-v1",
        "answer": (
            "Our demo shop is open Monday through Friday, 9 AM to 5 PM Central time. "
            "We are closed on weekends."
        ),
    },
    "2": {
        "title": "Diagnostic fee",
        "source": "pricing-v1",
        "answer": (
            "The demo diagnostic fee is 35 dollars. Staff provide repair estimates after inspection. "
            "This line cannot quote repair prices or take payment."
        ),
    },
    "3": {
        "title": "Supported services",
        "source": "services-v1",
        "answer": (
            "Our demo shop offers laptop, desktop, and printer diagnostics. "
            "We do not service phones or appliances. Staff must inspect a device "
            "before confirming a repair."
        ),
    },
}
