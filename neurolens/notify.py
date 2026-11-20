"""Emailing the operator about an access request, through the separate access-requests SNS
topic.

boto3 is imported inside the notifier, as elsewhere in `web/`, so importing this module never
loads AWS code or makes a call.
"""


def sns_notifier(region, topic_arn):
    """A `notify(request, email)` callable that publishes one access request to SNS."""

    def notify(request, email):
        import boto3

        note = request.get("note") or "(none)"
        message = "\n".join(
            [
                f"Requester: {email}",
                f"Platform: {request['platform']}",
                f"Channel: {request['channel_url']}",
                f"Note: {note}",
                "",
                "List waiting requests:",
                "python infra/access_requests.py list --backend data_api",
                "Grant credit: use the web admin panel (+5 min is the invite credit).",
            ]
        )
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic_arn,
            Subject=f"NeuroLens access request ({request['platform']})",
            Message=message,
        )

    return notify
