# NeuroLens: presentation script

Target: 450-500 words per presenter (~155 words/min). Slides 1-20 are the talk, 21 is Q&A, 22 the backup recording. `[TBD]` = fill from experiments (edit DATA in gen.py for charts).


## P1: 477 words, ~3:04

**Slide 1: Title** (117 words)

> Good afternoon, everyone, and thank you for joining us. We're team NeuroLens. Over the next fifteen minutes we'll show you a cloud service that sounds a little like science fiction: it predicts how a viewer's brain responds to a video, second by second, without putting anyone inside a scanner. The curve behind me is exactly what our model produces: one reading for every second of a video. I'll set up the problem and the idea. Then my teammates will run the whole thing live, explain the business model, and share how we tested it. Everything you'll see today is running on AWS right now, and you can try it yourself at the address on the last slide.

**Slide 2: Problem** (119 words)

> Let's start with a simple question: how do you know whether an ad works? Surveys are the usual answer, but people are bad at explaining their own reactions. Neuroscience does better. When researchers scan viewers with fMRI, the signal predicts real advertising results better than surveys or traditional biometrics. The catch is on the screen: testing a single commercial costs ten thousand dollars or more, and takes days to weeks. Webcam-based facial coding is cheaper, between about eight hundred and four thousand dollars, but still takes hours to days, and it only sees faces. The last row is our planned price: about one dollar for a thirty-second clip, in minutes. That gap is the whole reason this project exists.

**Slide 3: Why now** (112 words)

> And the gap is getting wider. Generative tools like Runway, Sora and CapCut mean a small team can now produce ten versions of an ad before lunch. Every one of those versions is something someone has to judge. Meanwhile, viewers watch in fast-scrolling feeds, where one swipe ends the ad. So it's not enough to have a good hook: you can lose someone at an awkward cut, or in a quiet stretch in the middle, like this dip on the attention curve. The question every creator is asking is: where exactly did I lose them, and which version is stronger? Production got cheap, but evaluation is still slow, expensive, and mostly guesswork.

**Slide 4: Idea** (129 words)

> So here's NeuroLens, in one line. You upload a video. It goes into TRIBE v2, a research model from Meta that combines three encoders, one for video, one for audio and one for speech, and was trained on real fMRI recordings of people watching films. For every second, it predicts activity at twenty thousand four hundred and eighty-four points on the cortex. We distil that into seven brain areas in three groups, and we do it twice, once with the sound and once muted. At the end, you get three things a creator can use: a timeline you can scrub, a 3D brain synced to the video, and a written report. This is a virtual pre-test, not a prediction of views or sales. I'll hand over to my teammate.


## P2: 495 words, ~3:11

**Slide 5: Users** (120 words)

> Thanks. Who is this for? Meet two people. Maya makes short videos. Her question is: which three seconds do I cut? She uploads two edits of her intro, the curve dips at her cold open, the report names the second, and she re-cuts and compares. The second is a four-person agency with three variants of a client's ad and one media budget. They upload all their cuts, compare any pair, and the verdict says where each one wins, so the decision rests on evidence rather than a hunch. The service supports batch upload of up to five files, and a written comparison for any two finished videos. These are personas, but the workflows are exactly what we'll show you live.

**Slide 6: Signals** (115 words)

> Here's what the service actually measures. We read seven brain areas and group them into three simple ideas. Seeing covers faces, bodies, scenes, and early visual cortex. Hearing covers the auditory areas, which tell us about sound. Understanding covers speech and social interaction. Every second is measured against what's typical for film and TV viewing, so zero on this scale is a typical moment, above means a stronger-than-typical response, and any two videos sit on the same scale. We also run the video a second time with the sound removed, which lets us say how much of the response comes from the soundtrack. In the demo, you'll see that for one ad it's almost half.

**Slide 7: Journey** (99 words)

> The journey has five steps. You sign in with Google. You upload a video, MP4, MOV or WebM up to two minutes, and the price is shown before you commit, with a short window to cancel. A GPU starts on demand and the model scores every second. You can watch live progress, stage by stage. Then you explore: the timeline, the 3D brain, the AI report, and side-by-side comparison. Credit is used in thirty-second steps, so a thirty-second clip costs the smallest unit. Rather than describe it any further, let me show you. I'm going to do this live.

**Slide 8: Demo 1** (161 words)

> I'm switching to the live site now. This is the address on the screen, and it's running on AWS in the cloud, not on my laptop. I'll sign in with my Google account. Once I'm in, you can see my credit balance at the top, and the upload area here. I'll choose this thirty-second clip. Notice that before anything is sent, the app tells me exactly how much credit this will use, and I get a short window to cancel. That matters, because GPU time costs real money. I'll confirm. The file goes straight from my browser to cloud storage. Now the job moves through stages: checking the file, waiting for a GPU, and analysing. A GPU is being started for us on demand, because nothing runs while nobody is using the service. We warmed one up before this session, so this takes about a minute. While it processes, I'll hand over to my colleague, who'll show you a finished result.


## P3: 464 words, ~3:00

**Slide 9: Demo 2** (175 words)

> Thanks. Here's a finished analysis, the thirty-second CeraVe ad, one of our free sample videos. On the left, the video plays, and everything on the right follows it. This orange line is the overall predicted brain response, compared with typical viewing. Watch the playhead: right around second six, when the actor holds up the product and looks into the camera, the line peaks, and the label reads well above typical. The orange dots are moments the system has flagged as notable. Underneath, the response is split into seeing, hearing, and understanding, all on the same scale, so you can see which channel drives the peak. Here it's seeing and hearing together, the face and the voice. Towards the end the line falls below typical. That's the closing product card, with no face and no speech. And this toggle shows the same video without sound. The page tells us forty-six percent of the response comes from the soundtrack. On the left, the 3D brain is synced to the video, and you can drag it to rotate.

**Slide 10: Demo 3** (118 words)

> Numbers are useful, but creators want advice. This Report tab is written by an AI model, which reads the brain data and the video frames together. Here it says: a strong celebrity-driven open, with the board-room wide shots and the long product card as the two opportunities. It lists strengths, improvements, and then specific moments with the second each one starts, what's on screen, and what the brain is doing. For agencies, the Compare tab puts two videos side by side. Here's the thirty-second cut against the extended cut of the same ad. The verdict says the short cut's direct opening gives it an early edge, but the two finish close. That's the pre-publish decision in one screen.

**Slide 11: Demo 4** (67 words)

> One more thing people always ask: what if someone uploads something that isn't a valid video? I'll try a plain text file renamed to MP4. It's checked before any GPU is started, so it's rejected within seconds, and no credit is charged. That's both a safety measure and a cost control. Failed analyses are also refunded automatically, so users never pay for a job that didn't deliver.

**Slide 12: Behind the scenes** (104 words)

> Briefly, here's what happens behind the scenes. This strip is an illustrative day. An owned GPU, the long grey bar, is paid for all twenty-four hours. Ours, the orange blocks, switches on only around the work: it starts when a video is waiting and stays on for seven idle minutes before shutting itself down. Three guards keep it safe. Every file is checked before a GPU is started. The GPU scales to zero. And a failed job refunds its credit automatically. The full architecture is in our report, so I'll leave it there and pass to my colleague, who'll explain the business behind it.


## P4: 491 words, ~3:11

**Slide 13: Business model** (152 words)

> Thanks. Let's talk about the business model, which comes in two phases, joined by a simple loop. Phase one is where we are today. The model we use, TRIBE v2, is released under a non-commercial licence, so we can't sell access to it, and we haven't. NeuroLens is free to explore: anyone can browse the samples, and creators and marketers with a channel of a thousand or more followers can request five free minutes. Then the loop turns. A creator adds the real audience-retention curve for a video that has been public for a week, and earns that analysis back as credit. For us, that's ground truth: real viewer behaviour to test the predictions against. Phase two is the commercial service. With a commercial licence, we'd sell prepaid credits, charged per second of video. We built and tested that version with Stripe in test mode, and kept it tagged in our repository.

**Slide 14: Unit economics** (113 words)

> What does one analysis cost us to deliver? Think of it as a sum, shown on the right. First, what the GPU does: how many GPU seconds a thirty-second video takes, times the hourly price. Second, what it takes to wake the GPU and let it rest: start-up time plus the seven idle minutes. Third, the AI report written by a language model. Fourth, storage and the database. Our preliminary estimate was under a cent of raw GPU for a thirty-second ad, and around ten cents fully loaded. The measured figure is [TBD] per video. Against a planned price of about one dollar per thirty-second video, that gives a gross margin of [TBD].

**Slide 15: SaaS vs on-premise** (118 words)

> Why cloud rather than buying a GPU? This model needs a lot of GPU memory, we measured a peak of almost twenty gigabytes, which is beyond many gaming cards, so on-premise means a professional workstation costing thousands of dollars upfront, plus power and maintenance. Here's the preliminary model. The grey line is that workstation, amortised to about two hundred and twenty-five dollars a month, flat no matter how much you use it. The orange line is cloud: a small fixed platform cost, plus a few cents for each video. They cross at roughly fifteen hundred videos a month. Below that, cloud is cheaper. Above it, owning starts to win. The measured GPU seconds will move that crossing point.

**Slide 16: Limits** (108 words)

> We also want to be clear about what we don't claim. NeuroLens predicts a typical viewer's brain response, second by second, on one scale for every video. It does not predict views, retention, or sales, and the research model gives no evidence for that, so we don't say it. It isn't emotion classification. It isn't cleared for commercial use, because the model licence is non-commercial, which is why a paid launch needs a new licence. And our usability study was small, five people, enough to find the main problems but not enough for statistical claims. Now I'll pass to my colleague, who'll show you how we tested it.


## P5: 492 words, ~3:11

**Slide 17: Latency** (111 words)

> Thanks. We evaluated the system in three ways. First, speed: how long does a user actually wait? We broke every job into stages: upload, waiting in the queue, downloading to the GPU, the model passes, and writing the results, and we measured them for fifteen, thirty and sixty second clips. Each stacked bar is one clip length. The takeaway is that [TBD: most of the time is the model itself]. It takes about [TBD] seconds of GPU time per second of video on the cloud GPU, compared with roughly five on a local high-end gaming card, per pass. And if no GPU is warm, the cold start adds about [TBD] minutes.

**Slide 18: Scaling** (115 words)

> Second, load. What happens when many people use it at once? We sent bursts of one, five, ten and twenty simultaneous uploads, with the GPU pre-warmed and cold, shown as two lines. [TBD: describe the finding, for example waiting time stays flat up to N users, then the queue absorbs the rest.] The key design property is that no job is ever dropped. Work waits in a queue, and users simply wait a little longer when demand exceeds capacity. For every burst we recorded user wait, queue wait, queue depth, and the number of GPUs running, so the chart shows the system scaling up and back down. A GPU failure refunds the job at once.

**Slide 19: Usability** (111 words)

> Third, usability. We ran a study with five participants from our target group, student creators. Each one signed in, uploaded a video, watched it process, and then used the timeline to find one moment where the response peaks and one where it drops. We measured task success and time on task, and gave them the System Usability Scale questionnaire. The average product scores sixty-eight, and eighty is the threshold for an A grade. Our average score was [TBD]. [TBD] of five completed the task without help, in a median of [TBD] seconds. One participant said: [TBD quote]. The biggest issue they raised was [TBD], and we've recorded it as future work.

**Slide 20: Conclusion** (120 words)

> To wrap up. A brain study that costs ten thousand dollars and takes weeks is now a few minutes on a cloud service, and when nobody's using it, almost nothing runs. What we showed you live was sign-in, upload with the price first, a second-by-second prediction, the 3D brain and AI report, a side-by-side comparison, and guardrails where bad files are rejected and failures refunded. Looking ahead, we have three priorities. One, secure a commercial licence so we can move to the paid phase. Two, use the retention curves our creators share to validate the predictions against real audience behaviour. And three, fix the usability issues our study uncovered. Thank you for listening. We'd be happy to take your questions.

**Slide 21: Q&amp;A** (35 words)

> Open for questions. Keep answers ready on: why this model and its licence, what the prediction does and does not mean, the cloud design and scale-up path, how refunds work, and what the experiments showed.


## ALL: 28 words, ~0:11

**Slide 22: Backup** (28 words)

> Press B. Say: 'The live site is having trouble, so here is the recording from this morning.' Then continue with the same talking points as the live demo.


**Total: 2419 words, ~15:37**
