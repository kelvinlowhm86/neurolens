# Walkthrough script (v6, for voice)

Purpose: uploads are offline (they need a paid GPU), so this video is how a portfolio visitor or
interviewer sees one real run and understands how it is built. The trailer sells the idea; this
shows proof, engineering judgement and enough of the design to make people open the live samples.

Rules: written for the ear. One story: follow Swee Choon's video through the system, so every
line leads to the next. "You" is always the creator; the system is described plainly, never as
"I". Concepts, no absolutes, nothing an interviewer can pick apart. Engineering terms only where
an engineer would naturally say them. AWS service names go on the diagram, not in the voice.
Every claim checked against the code or infra/terraform/README.md.

## 1. Hook (real page)
You made a video.
NeuroLens predicts how a brain will respond to it, second by second.
Let's put one through.

## 2. Upload (real page, cursor)
This is a TikTok from Swee Choon, a dim sum restaurant.
Drop it in, and you see the price before anything runs.
That amount is held from your credit, not spent.

## 3. Where the video goes (diagram: S3, Lambda, SQS)
So where does the video go?
Straight into storage, through a short-lived upload link. Big files never pass through the website.
The website just puts the job in a queue, and moves on.
It's asynchronous. Nothing waits on the GPU, so each side can scale, or fail, on its own.

## 4. A check first (diagram: dispatcher Lambda)
Before a GPU gets involved, a small function checks the file.
It measures the real length, instead of trusting the browser, and confirms the price.
A bad file never wakes a GPU.

## 5. Scale to zero (diagram: SageMaker, Lambda, Aurora Serverless; parts grey out as they sleep)
And the GPU only runs when there's work.
A job arrives, and it scales up. Things go quiet, and it scales back to zero.
The website and the database are serverless, so there's no server to look after.
They grow with traffic, and cost almost nothing when idle.
The trade-off is a few minutes' wait while the GPU starts.
Nobody needs this result in a second, so it's a trade worth making.

## 6. The model (diagram)
On the GPU, Meta's TRIBE model, trained on real brain scans, watches the video twice.
Once with sound, once on mute. You'll see why.

## 7. When things go wrong (diagram: dead-letter queue, reaper, GPU guard)
And when something goes wrong, you don't pay for it.
You're only charged once the result is saved. If the job fails, the hold goes back.
Billing is idempotent. If the same job arrives twice, it's only charged once.
And a watchdog makes sure no GPU is left running.

## 7b. Infrastructure as code (diagram: Terraform wraps the whole diagram)
All of this is infrastructure as code, in Terraform.
So the whole system can be reviewed, rebuilt, or torn down from code.
Every part gets least privilege. It can only do its own job.

## 8. The result (real page, large and calm)
About ten minutes later, here's the result.
The line shows how strong the response is. The brain shows where it's happening.
Anything above the dashed line is a stronger response than ordinary viewing.
It peaks at eight seconds. Click the peak, and the video jumps right to it.
She's telling the story over old photos.
But a lot of feeds autoplay on mute.
Turn the sound off, and the whole line drops.
Most of this response is coming from her voice.

## 9. The report (report tab; on screen: Claude on Amazon Bedrock, then the 0:08-0:13 "Do" line)
Then the report reads the whole thing for you, in plain words.
It goes moment by moment. What's on screen, what the brain is doing, and what to change.
It's written by Claude, on Amazon Bedrock.
For the old photos, it suggests cutting in a second of her face, reacting.
A face holds attention, even with the sound off.

## 10. Honest limit
None of this predicts your views.
It shows how a brain is likely to respond, second by second, so you can fix things before you post.

## 11. Close (real page)
The samples are live. Explore them at the link below.
