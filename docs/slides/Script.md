# NeuroLens: presentation script

Target: 450-500 words per presenter (~155 words/min). Slides 1-19 are the talk, 20 is Q&A. `[TBD]` = fill from the usability study (edit DATA in gen.py for charts). Numbers come from the final report.


## P1 · Business Need: 506 words, ~3:16

**Slide 1: Title** (62 words)

> Good afternoon, everyone. We're team NeuroLens, and we'll show you a cloud service that predicts how a viewer's brain responds to a video, second by second, without putting anyone in a scanner. I'll cover the business need and the brain model behind it. Then we'll cover the business model, a live demo, how we evaluated it, and the ethics and next steps.

**Slide 2: Problem** (88 words)

> Let's start with a simple question: how do you know whether an ad works? Surveys are the usual answer, but people are bad at explaining their own reactions. Brain scans do better, because they show how viewers really react, second by second. The catch is the price. An fMRI study costs ten to fifteen thousand dollars per advert and takes two to four weeks. NeuroLens costs one dollar for a thirty-second video and takes about seven minutes, which we measured. That gap is the reason this project exists.

**Slide 3: Why now** (65 words)

> And the gap is getting wider. Generative tools mean a small team can now produce ten versions of an ad before lunch, and every version has to be judged. Viewers watch in fast-scrolling feeds, where one swipe ends the ad, so you can lose someone at a quiet stretch, like this dip. Creators ask: where exactly did I lose them, and which version is stronger?

**Slide 4: Industry** (110 words)

> So what already exists? Three groups. First, lab studies: brain scans or EEG, run by research teams or suites like iMotions. They measure real brains, but cost a fortune and take weeks. Second, facial coding, from vendors like Realeyes, which reads viewers' faces. It is cheaper, but it reads faces, not brains. Third, AI attention tools such as Neurons AI and Attention Insight, which predict from the creative itself where eyes will land. They are fast, but they show what is noticed, not how a brain responds. NeuroLens sits in the gap: a predicted brain response for every second, with a second pass that removes the sound, for a dollar.

**Slide 5: TRIBE v2** (82 words)

> The model underneath is TRIBE v2, from Meta's AI research lab. It was trained on over a thousand hours of real fMRI from 720 people. It takes three inputs, frames, sound and words, through four models: V-JEPA 2 watches, Wav2Vec-BERT listens, and WhisperX and LLaMA 3.2 handle the speech. Out comes predicted activity at twenty thousand four hundred and eighty-four points on the cortex, every second. In Meta's paper it handles new videos and new people several times better than older models.

**Slide 6: Signals** (99 words)

> We turn those cortex points into seven signals in three groups, using brain areas from the Glasser atlas. Seeing covers faces, bodies, scenes and visual areas. Hearing covers sound. Understanding covers speech and social cues. This is a crop of our live site. The scale is the same on all three, and zero means typical film and TV viewing, so any two videos compare directly. A second pass with the sound removed shows what the soundtrack adds. Hold on to these three groups, because you'll see them move in the demo. Over to my teammate for the business model.


## P2 · Business Model: 362 words, ~2:20

**Slide 7: Business model** (114 words)

> Thanks. NeuroLens is for independent creators and small agencies who make many AI-generated versions of an advert and need to pick one before paying for media. It's pay as you go: one dollar for each started thirty seconds, from prepaid credit. New users get ten dollars, enough for ten videos. Why credit, not a card per video? A card fee of about 2.9 percent plus thirty cents takes about a third of a one-dollar charge, but only about four percent of a twenty-dollar pack. In our proof of concept an administrator grants the credit, and Stripe belongs to the production design. One caveat: TRIBE v2 is non-commercial, so a paid launch needs a licence.

**Slide 8: Unit economics** (90 words)

> What does one analysis cost us? About 393 seconds of GPU time on an L4 at one dollar twenty-two an hour: thirteen cents. Each session also pays for one GPU start-up and seven idle minutes, shared across its videos. With three videos in a session, a video costs us twenty-three cents. With thirty, fourteen cents. Against a one-dollar price, that is a gross margin of seventy-seven to eighty-six percent, plus about five dollars a month fixed. We hoped for ten cents on Spot GPUs, but Spot capacity was not available.

**Slide 9: SaaS vs on-premise** (158 words)

> Why cloud rather than buying a GPU? We priced a machine with one RTX 4090 at five thousand dollars, with a three-year life. The model peaked at 19.9 gigabytes, so a 24 gigabyte card is enough. With idle power, that is about one hundred fifty-nine dollars a month, flat, and almost nothing per video, about a third of a cent of electricity. AWS has no hardware cost and about five dollars a month fixed, but twenty-three cents a video. The lines cross at 670 videos a month. Below that, renting by the hour is cheaper, and ad testing comes in bursts: thirty versions one week, none the next month. Above it, owning wins: at a thousand videos a month over three years, that is about fifty-eight hundred dollars owned against eighty-five hundred on AWS. The price of renting is a wait, which you'll see in the demo and our results. Over to my colleague, who'll show it live.


## P3 · Demo: 493 words, ~3:11

**Slide 10: Demo** (493 words)

> Thanks. Now let me show you the real thing. This is the live site, running on AWS, and the picture on the slide is a link to it. I'll sign in with Google; email and password works too. Up here is my credit balance, and below it the upload area. I'll pick a thirty-second clip. Before anything is sent, the app tells me exactly what it will cost: one dollar for each started thirty seconds. I get a short window to cancel, because GPU time costs real money, and then I confirm. The file goes straight from my browser to cloud storage, and the job moves through stages I can watch: checking the file, waiting for a GPU, and analysing. We warmed a GPU up before this session, but even warm, a thirty-second clip takes about seven minutes, so I'll leave it running and open a finished sample: the thirty-second CeraVe ad, one of our free samples. On the left, the video plays with a 3D brain synced to it, and you can drag the brain to rotate it. Everything on the right follows the playhead. The orange line is the overall predicted response of an average viewer, compared with typical viewing. Around second six, when the actor holds up the product and looks at the camera, it peaks, and the label says well above typical. The orange dots mark moments the system flags as notable, and the strip of frames underneath shows what was on screen at each point. Now the three groups we just met. Seeing, hearing and understanding sit on the same scale, so you can see which one drives the peak. Here it is seeing and hearing together, the face and the voice, and you can open each group to see its parts. Towards the end the line falls below typical, on the closing product card, with no face and no speech. The toggle shows the same video without sound. That is the second pass I described, and the page tells us forty-six percent of the response comes from the soundtrack. Numbers are useful, but creators want advice. The Report tab is written by an AI model that reads the brain data and the video frames together. It lists strengths and improvements, then specific moments, with the second each one starts, what is on screen, and what the brain is doing. The Compare tab puts two videos side by side: here, the thirty-second cut against the longer cut of the same ad. The verdict says the short cut's direct opening gives it an early edge, but the two finish close. You can also download the results. One last thing people ask: what if someone uploads something that is not a video? I'll try a text file renamed to MP4. It is rejected within seconds, before any GPU starts, and nothing is charged. Failed jobs are refunded automatically too. Over to my colleague, who will show how we evaluated it.


## P4 · Evaluation: 423 words, ~2:44

**Slide 11: Validation** (152 words)

> Thanks. Now evaluation, starting with a simple question: how do we know it works? There are three layers. The model: its accuracy on new videos, new tasks and new people was shown by Meta's team in the TRIBE v2 paper. We did not repeat that against real scans, and we did not test whether the predictions relate to views, retention or sales, so we make no such claim. Our pipeline: we ran the same code and the same clip on our own RTX 4090 and on AWS, and across all eleven series, with and without sound, no value differs by more than 0.005. For billing, 77 of 80 automated refund tests refunded a failed job exactly once; the other three stopped on a GPU-library error during setup, and we did not kill a job on the live site. About 1,200 automated tests cover the code. Our users: the usability results come last.

**Slide 12: Latency** (98 words)

> Next, speed. We ran the same code and the same thirty-second clip on a machine we own and on AWS. The GPUs differ, so GPU work is compared per second of video: 3.7 seconds on the RTX 4090, against 10.7 on an A10G on AWS. End to end, a warm thirty-second clip takes 128 seconds on our machine and 415 on AWS. Upload takes about three seconds, the wait before the GPU starts about nineteen, and the rest is the model. After a long idle, the first video takes about 779 seconds, because a GPU has to start.

**Slide 13: Scaling** (86 words)

> Next, load. What happens when five people upload at the same moment? Both setups run one job at a time, so everyone queues. Here is AWS: each block is one person's job, running one after another, about 323 seconds each. The first finished at 853 seconds, and the last at 2,145, about thirty-six minutes after the upload. On our own machine, all five were done in 575 seconds. Nothing was dropped, people just wait longer. Serving more people at once would need a GPU quota increase.

**Slide 14: Usability** (87 words)

> Last, usability. At our booth, [N] visitors tried NeuroLens, a small formative test, and answered the System Usability Scale. The average product scores sixty-eight, and eighty is usually read as very good. Our mean score was [TBD], with a range of [TBD]. [x] percent said it showed them something they had not noticed, and [y] percent would pay one dollar to analyse a thirty-second video. One person said: [TBD quote]. The biggest issue raised was [TBD]. Now my colleague will cover ethics and where this goes next.


## P5 · Conclusion: 480 words, ~3:06

**Slide 15: Limits and ethics** (161 words)

> Thanks. Ethics and limits. What it is: an average viewer's predicted brain response, second by second, on one scale for every video, a virtual pre-test for editing. What it isn't: a prediction of views or sales, which we did not test. It isn't instant, since the first video after a quiet spell waits about six minutes for a GPU. And our usability study was small, enough to find the main problems but not to make statistical claims. On ethics: TRIBE v2 is non-commercial, so a paid launch needs a licence from Meta or a different model. No real person is scanned or profiled, because the model predicts an average viewer from the video alone. Still, a tool that shows what holds attention could be used to hold it at any cost, so we position it for checking edits, not for manipulating individuals. Our booth survey was anonymous and asked for consent first, and our report declares how we used AI tools.

**Slide 16: Where next** (153 words)

> Where does this go next? Our proof of concept runs one GPU at a time. The preliminary report described a larger design: containers behind a load balancer and a pool of EC2 GPU workers that scales with the queue. We built part of it, then moved the GPU to SageMaker after three findings. Spot did not deliver: all twenty-six launches failed for lack of capacity, and Spot was only one to nine percent cheaper. A smaller GPU is enough, since the model peaked at about twenty gigabytes. And the GPU sets the speed: an L40S finished a 119 second clip in 491 seconds, against 1,540 on an L4. The larger design adds about seventy dollars a month of fixed cost, so it only pays off above roughly a thousand videos a month. At today's volume SageMaker is the better choice, and the first step is to build it and measure its start-up time.

**Slide 17: Conclusion** (110 words)

> To wrap up. NeuroLens predicts a viewer's brain response to a video and returns it in about seven minutes for about one dollar, where an fMRI study costs ten thousand dollars or more and takes weeks. Below 670 videos a month, renting a GPU costs less than owning one. The trade-off is the wait: after a quiet spell, the first video waits about six minutes. The model predicts an average viewer, not views or sales. Our next steps are a commercial licence, the scaled-up design once we have built and measured it, and fixing what our usability study found. Thank you for listening. We'd be happy to take your questions.

**Slide 18: Q&amp;A** (56 words)

> Thank you for supporting us at STePS. Open for questions. Keep answers ready on: why this model and its licence, what the prediction does and does not mean, the cloud design and the cold-start wait, why SageMaker rather than Spot or our own GPU pool (production design slide), how refunds work, and what the experiments showed.


**Total: 2264 words, ~14:37**
