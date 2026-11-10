# NeuroLens: presentation script

Target: 450-500 words per presenter (~155 words/min). Slides 1-20 are the talk, 21 is Q&A, 22-23 are backups (recorded demo, production design). `[TBD]` = fill from the usability study (edit DATA in gen.py for charts). Numbers come from the final report.


## P1: 497 words, ~3:11

**Slide 1: Title** (117 words)

> Good afternoon, everyone, and thank you for joining us. We're team NeuroLens. Over the next fifteen minutes we'll show you a cloud service that sounds a little like science fiction: it predicts how a viewer's brain responds to a video, second by second, without putting anyone inside a scanner. The curve behind me is exactly what our model produces: one reading for every second of a video. I'll set up the problem and the idea. Then my teammates will run the whole thing live, explain the business model, and share how we tested it. Everything you'll see today is running on AWS right now, and you can try it yourself at the address on the last slide.

**Slide 2: Problem** (133 words)

> Let's start with a simple question: how do you know whether an ad works? Surveys are the usual answer, but people are bad at explaining their own reactions. Brain scans do better, because they show how viewers really react, second by second. The catch is the price. An fMRI study costs ten to fifteen thousand dollars per advert and takes two to four weeks. Webcam facial coding is cheaper, between eight hundred fifty and four thousand dollars a cut, but takes three to five business days and only sees faces. AI attention maps are quick and cheap, but they are heuristic heatmaps, not a brain response. The last row is NeuroLens: one dollar for a thirty-second video, and about seven minutes, which we measured. That gap is the whole reason this project exists.

**Slide 3: Why now** (112 words)

> And the gap is getting wider. Generative tools like Runway, Sora and CapCut mean a small team can now produce ten versions of an ad before lunch. Every one of those versions is something someone has to judge. Meanwhile, viewers watch in fast-scrolling feeds, where one swipe ends the ad. So it's not enough to have a good hook: you can lose someone at an awkward cut, or in a quiet stretch in the middle, like this dip on the attention curve. The question every creator is asking is: where exactly did I lose them, and which version is stronger? Production got cheap, but evaluation is still slow, expensive, and mostly guesswork.

**Slide 4: Idea** (135 words)

> So here's NeuroLens, in one line. You upload a video. It goes into TRIBE v2, a research model from Meta that predicts brain activity from video. Under the hood, WhisperX turns the speech into words, LLaMA 3.2 reads them, Wav2Vec-BERT listens to the sound and V-JEPA 2 watches the frames. For every second, it predicts activity at twenty thousand four hundred and eighty-four points on the cortex. We average that over brain areas from the Glasser atlas to get seven signals in three groups, and we do it twice, once with the sound and once muted. A creator gets three things: a timeline you can scrub, a 3D brain synced to the video, and a written report. This is a virtual pre-test, not a prediction of views or sales. I'll hand over to my teammate.


## P2: 505 words, ~3:15

**Slide 5: Users** (120 words)

> Thanks. Who is this for? Meet two people. Maya makes short videos. Her question is: which three seconds do I cut? She uploads two edits of her intro, the curve dips at her cold open, the report names the second, and she re-cuts and compares. The second is a four-person agency with three variants of a client's ad and one media budget. They upload all their cuts, compare any pair, and the verdict says where each one wins, so the decision rests on evidence rather than a hunch. The service supports batch upload of up to five files, and a written comparison for any two finished videos. These are personas, but the workflows are exactly what we'll show you live.

**Slide 6: Signals** (120 words)

> Here's what the service actually measures. Each signal is the average predicted activity in a set of brain areas from the Glasser atlas, and we group the seven signals into three simple ideas. Seeing covers faces, bodies, scenes, and visual areas. Hearing covers sound. Understanding covers speech and social interaction. Every second is shown against typical film and TV viewing: zero is a typical moment, above means a stronger-than-typical response, and any two videos sit on the same scale, so they can be compared directly. We also run the video a second time with the sound removed, which shows how much of the response comes from the soundtrack. In the demo, you'll see that for one ad it's almost half.

**Slide 7: Journey** (108 words)

> The journey has five steps. You sign in with Google, or with an email and password. New users get ten dollars of invite credit, enough for ten thirty-second videos. You upload a video, MP4, MOV or WebM up to two minutes. The price is one dollar for each started thirty seconds, shown before you commit, with a short window to cancel. A GPU starts on demand and the model scores every second, with live progress, stage by stage. Then you explore: the timeline, the 3D brain, the AI report, and side-by-side comparison. Rather than describe it any further, let me show you. I'm going to do this live.

**Slide 8: Demo 1** (157 words)

> I'm switching to the live site now. This is the address on the screen, and it's running on AWS in the cloud, not on my laptop. I'll sign in with my Google account. Once I'm in, you can see my credit balance at the top, and the upload area here. I'll choose this thirty-second clip. Before anything is sent, the app tells me exactly how much credit this will use, and I get a short window to cancel. That matters, because GPU time costs real money. I'll confirm. The file goes straight from my browser to cloud storage. Now the job moves through stages: checking the file, waiting for a GPU, and analysing. We warmed a GPU up before this session, because a cold start takes about six minutes. Even warm, a thirty-second clip takes about seven minutes to finish, so I'll leave it running and hand over to my colleague, who'll show you a finished result.


## P3: 489 words, ~3:10

**Slide 9: Demo 2** (175 words)

> Thanks. Here's a finished analysis, the thirty-second CeraVe ad, one of our free sample videos. On the left, the video plays, and everything on the right follows it. This orange line is the overall predicted brain response, compared with typical viewing. Watch the playhead: right around second six, when the actor holds up the product and looks into the camera, the line peaks, and the label reads well above typical. The orange dots are moments the system has flagged as notable. Underneath, the response is split into seeing, hearing, and understanding, all on the same scale, so you can see which channel drives the peak. Here it's seeing and hearing together, the face and the voice. Towards the end the line falls below typical. That's the closing product card, with no face and no speech. And this toggle shows the same video without sound. The page tells us forty-six percent of the response comes from the soundtrack. On the left, the 3D brain is synced to the video, and you can drag it to rotate.

**Slide 10: Demo 3** (118 words)

> Numbers are useful, but creators want advice. This Report tab is written by an AI model, which reads the brain data and the video frames together. Here it says: a strong celebrity-driven open, with the board-room wide shots and the long product card as the two opportunities. It lists strengths, improvements, and then specific moments with the second each one starts, what's on screen, and what the brain is doing. For agencies, the Compare tab puts two videos side by side. Here's the thirty-second cut against the extended cut of the same ad. The verdict says the short cut's direct opening gives it an early edge, but the two finish close. That's the pre-publish decision in one screen.

**Slide 11: Demo 4** (67 words)

> One more thing people always ask: what if someone uploads something that isn't a valid video? I'll try a plain text file renamed to MP4. It's checked before any GPU is started, so it's rejected within seconds, and no credit is charged. That's both a safety measure and a cost control. Failed analyses are also refunded automatically, so users never pay for a job that didn't deliver.

**Slide 12: Behind the scenes** (129 words)

> Briefly, here's what happens behind the scenes. Analysing a video takes minutes, longer than a web request can wait, so the website never waits for the GPU. Your browser uploads straight to S3, a queue holds the notice, and a Lambda function checks the video before handing it to a SageMaker endpoint. On this illustrative day, an owned GPU, the grey bar, is paid for around the clock. Ours, the orange blocks, starts only when a video is waiting and stops after seven idle minutes, so the service costs about five dollars a month when idle. Credit is reserved, then charged or refunded exactly once, so a failed job costs nothing. The full architecture is in our report. I'll pass to my colleague, who'll explain the business behind it.


## P4: 501 words, ~3:15

**Slide 13: Business model** (142 words)

> Thanks. Let's talk about the business model. NeuroLens is for independent creators and small agencies who make many AI-generated versions of an advert and need to pick one before paying for media. It's pay as you go: users buy prepaid credit and pay one dollar for each started thirty seconds of video. New users get ten dollars of invite credit, enough for ten thirty-second videos. Why credit rather than charging a card per video? A card fee of about 2.9 percent plus thirty cents would take about a third of a one-dollar charge, but only about four percent of a twenty-dollar pack. In our proof of concept an administrator grants the credit; card payment belongs to the production design. One caveat: TRIBE v2 is released for non-commercial use, so a paid launch would need a licence from Meta, or a different model.

**Slide 14: Unit economics** (108 words)

> What does one analysis cost us? Each video needs about 393 seconds of GPU time on the L4 we were given, at one dollar twenty-two an hour. That is about thirteen cents. On top of that, each session pays for one GPU start-up plus seven idle minutes, shared across its videos. If a team sends three videos in a session, we pay twenty-three cents a video. If it sends thirty, fourteen cents. Fixed costs are about five dollars a month. Against a one-dollar price, that is a gross margin of seventy-seven to eighty-six percent. We hoped for ten cents on Spot GPUs, but Spot capacity was not available.

**Slide 15: SaaS vs on-premise** (146 words)

> Why cloud rather than buying a GPU? We priced a machine with one RTX 4090 at five thousand dollars, with a three-year life. The model peaked at 19.9 gigabytes, so a 24 gigabyte card is enough. With idle power, that is about one hundred fifty-nine dollars a month, flat, and almost nothing per video, about a third of a cent of electricity. AWS has no hardware cost and about five dollars a month fixed, but twenty-three cents a video. The lines cross at 670 videos a month. Below that, renting by the hour is cheaper, and ad testing comes in bursts: thirty versions one week, none the next month. Above it, owning wins: at a thousand videos a month over three years, that is about fifty-eight hundred dollars owned against eighty-five hundred on AWS. The price of renting is the wait, which my colleague will show.

**Slide 16: Limits** (105 words)

> We also want to be clear about what we don't claim. NeuroLens predicts an average viewer's brain response, second by second, on one scale for every video. It does not predict views or sales, and we did not test that. It isn't instant: after a quiet spell, the first video waits about six minutes for a GPU to start. It isn't cleared for commercial use, because the model licence is non-commercial. And our usability study was a small formative test, enough to find the main problems but not enough for statistical claims. Now I'll pass to my colleague, who'll show you how we tested it.


## P5: 513 words, ~3:18

**Slide 17: Latency** (127 words)

> Thanks. We evaluated the system in three ways. First, speed. We ran the same code and the same thirty-second clip on a machine we own and on AWS. The GPUs differ, so GPU work is compared per second of video: 3.7 seconds on the RTX 4090, against 10.7 on an A10G on AWS. End to end, a warm thirty-second clip takes 128 seconds on our machine and 415 on AWS. Upload takes about three seconds, the wait before the GPU starts about nineteen, and the rest is the model. After a long idle, the first video takes about 779 seconds, because a GPU has to start. Both setups give the same result: across all eleven series, with and without sound, no value differs by more than 0.005.

**Slide 18: Scaling** (102 words)

> Second, load. What happens when several people upload at once? We sent five uploads at the same time. Both setups run one job at a time, so the jobs queue. On AWS they finished about 323 seconds apart, the last after 2,145 seconds. On our machine the last finished after 575. Nothing was dropped. For failures, in 77 of our 80 automated refund tests a failed or killed job was refunded exactly once; the other three stopped on a GPU-library error during setup. We did not kill a job on the live deployment. More simultaneous users would need a GPU quota increase.

**Slide 19: Usability** (111 words)

> Third, usability. We ran a study with five participants from our target group, student creators. Each one signed in, uploaded a video, watched it process, and then used the timeline to find one moment where the response peaks and one where it drops. We measured task success and time on task, and gave them the System Usability Scale questionnaire. The average product scores sixty-eight, and eighty is the threshold for an A grade. Our average score was [TBD]. [TBD] of five completed the task without help, in a median of [TBD] seconds. One participant said: [TBD quote]. The biggest issue they raised was [TBD], and we've recorded it as future work.

**Slide 20: Conclusion** (125 words)

> To wrap up. NeuroLens predicts a viewer's brain response to a video and returns it in about seven minutes for about one dollar, where an fMRI study costs ten thousand dollars or more and takes weeks. Below 670 videos a month, renting a GPU costs less than owning one. The trade-off is the wait: after a quiet spell, the first video waits about six minutes for a GPU. The model predicts an average viewer, not views or sales. Looking ahead: a commercial licence; a production design that scales with the queue, which we estimate pays off above about a thousand videos a month but have not built; and fixing what our usability study found. Thank you for listening. We'd be happy to take your questions.

**Slide 21: Q&amp;A** (48 words)

> Open for questions. Keep answers ready on: why this model and its licence, what the prediction does and does not mean, the cloud design and the cold-start wait, why SageMaker rather than Spot or our own GPU pool (backup slide), how refunds work, and what the experiments showed.


## ALL: 189 words, ~1:13

**Slide 22: Backup** (28 words)

> Press B. Say: 'The live site is having trouble, so here is the recording from this morning.' Then continue with the same talking points as the live demo.

**Slide 23: Backup: production design** (161 words)

> Backup for questions about scaling. The preliminary report described a larger design: stateless Flask containers on Fargate behind a load balancer, and a pool of EC2 GPU workers that scales with the queue backlog. We built the Auto Scaling group with CPU instances, then moved the GPU to SageMaker after three findings. Spot did not deliver: all 26 launches failed for lack of capacity, and Spot was only one to nine percent cheaper. A smaller GPU is enough, since the model peaked at about twenty gigabytes. And the GPU sets the speed: an L40S finished a 119 second clip in 491 seconds against 1,540 on an L4. SageMaker charges about forty percent more per GPU-hour than EC2 for the same L40S, but the production design adds about seventy dollars a month of fixed cost. It only becomes cheaper above roughly a thousand videos a month, and we have not measured its start-up time. At today's volume, SageMaker is the better choice.


**Total: 2505 words, ~16:09**
