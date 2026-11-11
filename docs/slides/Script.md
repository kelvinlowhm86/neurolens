# NeuroLens: presentation script

Target: 450-500 words per presenter (~155 words/min). Slides 1-19 are the talk (19 is Q&A), 20 is the backup recording. `[TBD]` = fill from the usability study (edit DATA in gen.py for charts). Numbers come from the final report.


## P1 · Business need: 492 words, ~3:10

**Slide 1: Title** (86 words)

> Good afternoon, everyone, and thank you for joining us. We're team NeuroLens. Over the next fifteen minutes we'll show you a cloud service that predicts how a viewer's brain responds to a video, second by second, without putting anyone inside a scanner. I'll cover the business need: the problem, what already exists, and the brain model we build on. My teammates will then cover the business model, a live demo, how we evaluated it, and the ethics and next steps. Everything runs on AWS right now.

**Slide 2: Problem** (88 words)

> Let's start with a simple question: how do you know whether an ad works? Surveys are the usual answer, but people are bad at explaining their own reactions. Brain scans do better, because they show how viewers really react, second by second. The catch is the price. An fMRI study costs ten to fifteen thousand dollars per advert and takes two to four weeks. NeuroLens costs one dollar for a thirty-second video and takes about seven minutes, which we measured. That gap is the reason this project exists.

**Slide 3: Why now** (73 words)

> And the gap is getting wider. Generative tools mean a small team can now produce ten versions of an ad before lunch, and every version has to be judged. Meanwhile, viewers watch in fast-scrolling feeds, where one swipe ends the ad, so you can lose someone at an awkward cut or a quiet stretch, like this dip on the curve. Creators ask: where exactly did I lose them, and which version is stronger?

**Slide 4: Industry** (130 words)

> So what already exists? Three groups. First, lab studies: brain scans or EEG, run by research teams or suites like iMotions. They measure real brains, but cost a fortune and take weeks. Second, facial coding, from vendors like Realeyes: a webcam reads viewers' faces. It is cheaper, but it reads faces, not brains, and needs real viewers every time. Third, AI attention tools such as Neurons AI and Attention Insight, which predict from the creative itself where eyes will land. They are fast, but they show what is noticed, not how a brain responds. NeuroLens sits in the gap: a predicted brain response for every second, with a second pass that removes the sound, for a dollar. And every video is on the same scale, so two cuts compare directly.

**Slide 5: TRIBE v2** (115 words)

> The brain model underneath is TRIBE v2, from Meta's AI research lab, published in 2026. It was trained on over a thousand hours of real fMRI recordings from 720 people, on naturalistic video, audio and language. It takes three inputs: the frames, the sound and the words, through four models. V-JEPA 2 watches, Wav2Vec-BERT listens, and WhisperX and LLaMA 3.2 handle the speech. It predicts activity at twenty thousand four hundred and eighty-four points on the cortex every second, and handles new videos and new people several times more accurately than linear models. Two limits to remember: it predicts an average viewer, and its licence is non-commercial. Over to my teammate for the business model.


## P2 · Business model: 411 words, ~2:39

**Slide 6: Business model** (145 words)

> Thanks. Let's talk about the business model. NeuroLens is for independent creators and small agencies: a short-form creator deciding which three seconds to cut, or an agency with several AI-generated cuts and one media budget. It's pay as you go: users buy prepaid credit and pay one dollar for each started thirty seconds of video. New users get ten dollars of invite credit, enough for ten thirty-second videos. Why credit rather than charging a card per video? A card fee of about 2.9 percent plus thirty cents would take about a third of a one-dollar charge, but only about four percent of a twenty-dollar pack. In our proof of concept an administrator grants the credit; card payment belongs to the production design. One caveat: TRIBE v2 is released for non-commercial use, so a paid launch would need a licence from Meta, or a different model.

**Slide 7: Unit economics** (108 words)

> What does one analysis cost us? Each video needs about 393 seconds of GPU time on the L4 we were given, at one dollar twenty-two an hour. That is about thirteen cents. On top of that, each session pays for one GPU start-up plus seven idle minutes, shared across its videos. If a team sends three videos in a session, we pay twenty-three cents a video. If it sends thirty, fourteen cents. Fixed costs are about five dollars a month. Against a one-dollar price, that is a gross margin of seventy-seven to eighty-six percent. We hoped for ten cents on Spot GPUs, but Spot capacity was not available.

**Slide 8: SaaS vs on-premise** (158 words)

> Why cloud rather than buying a GPU? We priced a machine with one RTX 4090 at five thousand dollars, with a three-year life. The model peaked at 19.9 gigabytes, so a 24 gigabyte card is enough. With idle power, that is about one hundred fifty-nine dollars a month, flat, and almost nothing per video, about a third of a cent of electricity. AWS has no hardware cost and about five dollars a month fixed, but twenty-three cents a video. The lines cross at 670 videos a month. Below that, renting by the hour is cheaper, and ad testing comes in bursts: thirty versions one week, none the next month. Above it, owning wins: at a thousand videos a month over three years, that is about fifty-eight hundred dollars owned against eighty-five hundred on AWS. The price of renting is a wait, which you'll see in the demo and our results. Over to my colleague, who'll show it live.


## P3 · Demo: 466 words, ~3:00

**Slide 9: What it does** (112 words)

> Thanks. So what do you get? You upload a video, up to two minutes. TRIBE v2 predicts the brain's response for every second, and we turn that into seven signals in three groups: seeing, which covers faces, bodies, scenes and visual areas; hearing, which is sound; and understanding, which covers speech and social cues. Everything is shown against typical film and TV viewing, where zero is typical, so any two videos share one scale. We also run a second pass with the sound removed, to show how much the soundtrack contributes. The outputs are a timeline, a 3D brain synced to the video, and an AI-written report. Now let me show you.

**Slide 10: Demo** (225 words)

> This is the real site, running on AWS, and the picture is a link to it. I sign in with Google and upload a thirty-second clip. The app shows the price first, one dollar for each started thirty seconds, with a short window to cancel, because GPU time costs money. I confirm, and the file goes straight to cloud storage. We warmed a GPU up, but a warm job still takes about seven minutes, so I'll leave it running and open a finished sample: the thirty-second CeraVe ad. The video plays beside a 3D brain synced to it. The orange line is the overall predicted response against typical viewing. Around second six, when the actor holds up the product and looks at the camera, it peaks. Underneath, seeing, hearing and understanding show which channel drives it. Towards the end it falls below typical, on the product card with no face or speech. The toggle shows the video without sound, and the page says forty-six percent of the response comes from the soundtrack. The Report tab is written by an AI model that reads the brain data and frames together and names specific seconds. The Compare tab sets the thirty-second cut beside the longer cut, with a verdict. And a text file renamed to MP4 is rejected within seconds, before any GPU starts, with nothing charged.

**Slide 11: Behind the scenes** (129 words)

> Briefly, here's what happens behind the scenes. Analysing a video takes minutes, longer than a web request can wait, so the website never waits for the GPU. Your browser uploads straight to S3, a queue holds the notice, and a Lambda function checks the video before handing it to a SageMaker endpoint. On this illustrative day, an owned GPU, the grey bar, is paid for around the clock. Ours, the orange blocks, starts only when a video is waiting and stops after seven idle minutes, so the service costs about five dollars a month when idle. Credit is reserved, then charged or refunded exactly once, so a failed job costs nothing. The full architecture is in our report. I'll pass to my colleague, who'll show how we evaluated it.


## P4 · Evaluation: 402 words, ~2:36

**Slide 12: Validation** (152 words)

> Thanks. Now evaluation, starting with a simple question: how do we know it works? There are three layers. The model: its accuracy on new videos, new tasks and new people was shown by Meta's team in the TRIBE v2 paper. We did not repeat that against real scans, and we did not test whether the predictions relate to views, retention or sales, so we make no such claim. Our pipeline: we ran the same code and the same clip on our own RTX 4090 and on AWS, and across all eleven series, with and without sound, no value differs by more than 0.005. For billing, 77 of 80 automated refund tests refunded a failed job exactly once; the other three stopped on a GPU-library error during setup, and we did not kill a job on the live site. About 1,200 automated tests cover the code. Our users: the usability results come last.

**Slide 13: Latency** (98 words)

> Next, speed. We ran the same code and the same thirty-second clip on a machine we own and on AWS. The GPUs differ, so GPU work is compared per second of video: 3.7 seconds on the RTX 4090, against 10.7 on an A10G on AWS. End to end, a warm thirty-second clip takes 128 seconds on our machine and 415 on AWS. Upload takes about three seconds, the wait before the GPU starts about nineteen, and the rest is the model. After a long idle, the first video takes about 779 seconds, because a GPU has to start.

**Slide 14: Scaling** (65 words)

> Next, load. What happens when several people upload at once? We sent five uploads at the same time. Both setups run one job at a time, so the jobs queue. On AWS they finished about 323 seconds apart, the last after 2,145 seconds. On our machine the last finished after 575. Nothing was dropped. Serving more people at once would need a GPU quota increase.

**Slide 15: Usability** (87 words)

> Last, usability. At our booth, [N] visitors tried NeuroLens, a small formative test, and answered the System Usability Scale. The average product scores sixty-eight, and eighty is usually read as very good. Our mean score was [TBD], with a range of [TBD]. [x] percent said it showed them something they had not noticed, and [y] percent would pay one dollar to analyse a thirty-second video. One person said: [TBD quote]. The biggest issue raised was [TBD]. Now my colleague will cover ethics and where this goes next.


## P5 · Ethics and conclusion: 473 words, ~3:03

**Slide 16: Limits and ethics** (161 words)

> Thanks. Ethics and limits. What it is: an average viewer's predicted brain response, second by second, on one scale for every video, a virtual pre-test for editing. What it isn't: a prediction of views or sales, which we did not test. It isn't instant, since the first video after a quiet spell waits about six minutes for a GPU. And our usability study was small, enough to find the main problems but not to make statistical claims. On ethics: TRIBE v2 is non-commercial, so a paid launch needs a licence from Meta or a different model. No real person is scanned or profiled, because the model predicts an average viewer from the video alone. Still, a tool that shows what holds attention could be used to hold it at any cost, so we position it for checking edits, not for manipulating individuals. Our booth survey was anonymous and asked for consent first, and our report declares how we used AI tools.

**Slide 17: Production design** (153 words)

> Where does this go next? Our proof of concept runs one GPU at a time. The preliminary report described a larger design: containers behind a load balancer and a pool of EC2 GPU workers that scales with the queue. We built part of it, then moved the GPU to SageMaker after three findings. Spot did not deliver: all twenty-six launches failed for lack of capacity, and Spot was only one to nine percent cheaper. A smaller GPU is enough, since the model peaked at about twenty gigabytes. And the GPU sets the speed: an L40S finished a 119 second clip in 491 seconds, against 1,540 on an L4. The larger design adds about seventy dollars a month of fixed cost, so it only pays off above roughly a thousand videos a month. At today's volume SageMaker is the better choice, and the first step is to build it and measure its start-up time.

**Slide 18: Conclusion** (110 words)

> To wrap up. NeuroLens predicts a viewer's brain response to a video and returns it in about seven minutes for about one dollar, where an fMRI study costs ten thousand dollars or more and takes weeks. Below 670 videos a month, renting a GPU costs less than owning one. The trade-off is the wait: after a quiet spell, the first video waits about six minutes. The model predicts an average viewer, not views or sales. Our next steps are a commercial licence, the scaled-up design once we have built and measured it, and fixing what our usability study found. Thank you for listening. We'd be happy to take your questions.

**Slide 19: Q&amp;A** (49 words)

> Open for questions. Keep answers ready on: why this model and its licence, what the prediction does and does not mean, the cloud design and the cold-start wait, why SageMaker rather than Spot or our own GPU pool (production design slide), how refunds work, and what the experiments showed.


## ALL: 28 words, ~0:11

**Slide 20: Backup** (28 words)

> Press B. Say: 'The live site is having trouble, so here is the recording from this morning.' Then continue with the same talking points as the live demo.


**Total: 2244 words, ~14:28**
