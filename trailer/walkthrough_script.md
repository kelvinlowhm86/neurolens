# Walkthrough script (v6, for voice)

Purpose: uploads are offline (they need a paid GPU), so this video is how a portfolio visitor or
interviewer sees one real run and understands how it is built. The trailer sells the idea; this
shows proof, engineering judgement and enough of the design to make people open the live samples.

Rules: written for the ear. One story: follow Swee Choon's video through the system. Each line
answers the question a technical viewer is likely to ask next ("so where does it go?", "do you
trust the browser?", "is a GPU on all day?"), and each design choice comes with its reason and,
where there is one, its trade-off. "You" is always the creator; the system is described plainly,
never as "I". The audience is technical, so AWS service names are said in the voice as well as
shown on the diagram. Concepts, no absolutes, nothing an interviewer can pick apart. Every claim
checked against the code or infra/terraform/README.md. The viewer's question for each section is
in brackets; it is not spoken.

## 1. Hook (real page)
You made a video.
NeuroLens predicts how a brain will respond to it, second by second.
Let's put one through.

## 2. Upload (real page, cursor)
This is a TikTok from Swee Choon, a dim sum restaurant.
Drop it in, and you see the price before anything runs.
That amount is held from your credit, not spent.

## 3. Where the video goes (diagram: S3, Lambda, SQS; small caption "presigned URL" on the upload arc)
[So where does the video go?]
The video goes straight into S3, through a short-lived upload link. It never passes through the
website, so a big file can't slow it down for anyone else.
The website, a Lambda function, just puts a job on an SQS queue, and it's done.
It's asynchronous. Nothing waits on the GPU, so each side can scale, or fail, on its own.

## 4. A check first (diagram: dispatcher Lambda; small on-screen caption "ffprobe")
[Do you just trust whatever the browser sends?]
Before any money is spent on a GPU, another Lambda opens the file and reads its real length,
rather than trusting what the browser said. It works the price out again from that.
If the file won't open, runs too long, or there isn't enough credit, you're refunded right there,
and no GPU ever wakes up.

## 5. Scale to zero (diagram: SageMaker; GPU greys out when idle)
[So is a GPU running all day?]
GPUs are expensive, so none is left running. The model runs on SageMaker, which starts a GPU when
a job arrives, and scales back to zero when things go quiet.
The catch is that starting one takes a few minutes. For a chatbot, that would be a problem. But
you're not waiting for a reply here; you're getting an analysis to study. So a few minutes is a
fair price for paying nothing while nobody's using it.

## 6. The model (diagram)
[What actually runs on the GPU?]
Once it's up, it runs TRIBE, a research model from Meta, trained on brain scans from over seven
hundred people, so it can predict how a brain responds to a video it's never seen.

## 7. When things go wrong (diagram: retry, dead-letter queue, watchdog Lambda)
[What if it breaks? Do I lose my money?]
And if something breaks halfway? It tries once more. If it fails again, the job goes to a
dead-letter queue, and your hold goes straight back. You're only charged once the result is saved.
SQS can occasionally deliver the same job twice, so billing is idempotent: one job, one charge.
And a watchdog Lambda makes sure a stuck job can't leave a GPU quietly running up a bill.

## 7b. Infrastructure as code (diagram: Terraform wraps the whole diagram)
[Could you rebuild this?]
All of it is written as code, in Terraform, so the whole system can be reviewed, rebuilt, or torn
down in one go.

## 8. The result (real page, large and calm)
About ten minutes later, here's the result.
The line shows how strong the response is, and the brain shows where.
Click the peak, and the video jumps right to it.

## 9. The report (report card; caption "Claude · Amazon Bedrock", then the 0:08-0:13 "Do" line)
[Do I have to read the chart myself?]
Then Claude, on Amazon Bedrock, writes it up moment by moment, with what to change.
For the old photos, it suggests cutting in a second of her face.

## 10. Honest limit
[So will this get me views?]
None of this predicts your views.
It shows how a brain is likely to respond, second by second, so you can fix things before you post.

## 11. Close (real page)
The samples are live. Explore them at the link below.
