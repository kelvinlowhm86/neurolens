"""SQS access for the worker: receive, keep-alive, delete, and Spot requeue."""

from dataclasses import dataclass


@dataclass
class Message:
    id: str
    receipt: str
    body: str
    receive_count: int


class JobQueue:
    def __init__(self, sqs_client, queue_url: str):
        self.sqs = sqs_client
        self.url = queue_url

    def receive(self, wait_s: int = 20, visibility_s: int = 300) -> Message | None:
        """Long-poll for one message (one job per GPU at a time)."""
        resp = self.sqs.receive_message(
            QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_s,
            VisibilityTimeout=visibility_s, AttributeNames=["ApproximateReceiveCount"],
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            return None
        m = msgs[0]
        return Message(id=m["MessageId"], receipt=m["ReceiptHandle"], body=m["Body"],
                       receive_count=int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1)))

    def extend(self, msg: Message, seconds: int) -> None:
        """Keep the message hidden while the job is still running."""
        self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=msg.receipt, VisibilityTimeout=seconds)

    def retry_soon(self, msg: Message, seconds: int = 30) -> None:
        """Transient failure: make the message visible again shortly (counts toward the DLQ limit)."""
        self.extend(msg, seconds)

    def delete(self, msg: Message) -> None:
        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=msg.receipt)

    def requeue_fresh(self, msg: Message, reason: str) -> None:
        """
        Spot interruption: send a fresh copy and delete the original. The copy starts with a
        receive count of 1, so an interruption does not use up one of the job's 2 attempts.
        """
        self.sqs.send_message(
            QueueUrl=self.url, MessageBody=msg.body,
            MessageAttributes={"requeue_reason": {"DataType": "String", "StringValue": reason}},
        )
        self.delete(msg)

    def visible_count(self) -> int:
        attrs = self.sqs.get_queue_attributes(
            QueueUrl=self.url, AttributeNames=["ApproximateNumberOfMessages"]
        )["Attributes"]
        return int(attrs["ApproximateNumberOfMessages"])
