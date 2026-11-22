# NeuroLens: presentation script

Target: 450-500 words per presenter (~155 words/min). Slides 1-20 are the talk, 21 is Q&A. `[TBD]` = fill from the usability study (edit DATA in gen.py for charts). Numbers come from the final report.


## P1 · Business Need: 500 words, ~3:13

**Slide 1: Title** (62 words)

> Good afternoon, everyone. We're team NeuroLens, and we'll show you a cloud service that predicts how a viewer's brain responds to a video, second by second, without putting anyone in a scanner. I'll cover the business need and the brain model behind it. Then we'll cover the business model, a live demo, how we evaluated it, and the ethics and next steps.

**Slide 2: Problem** (88 words)

> Let's start with a simple question: how do you know whether an ad works? Surveys are the usual answer, but people are bad at explaining their own reactions. Brain scans do better, because they show how viewers really react, second by second. The catch is the price. An fMRI study costs ten to fifteen thousand dollars per advert and takes two to four weeks. NeuroLens costs one dollar for a thirty-second video and takes about seven minutes, which we measured. That gap is the reason this project exists.

**Slide 3: Why now** (70 words)

> And the gap is getting wider. Generative tools like Runway, Sora and CapCut mean a small team can now produce dozens of versions of an ad quickly, and every version has to be judged. Viewers watch in fast-scrolling feeds, where one swipe ends the ad, so you can lose someone at a quiet stretch, like this dip. Creators ask: where exactly did I lose them, and which version is stronger?

**Slide 4: Industry** (99 words)

> So what already exists? Three groups. First, physical fMRI studies: real brain scans, but expensive and slow. Second, facial coding, from vendors like Realeyes and iMotions, which read viewers' faces from a webcam. It is cheaper, but it reads faces, not brains. Third, AI attention tools such as Neurons and Dragonfly AI, which predict from the creative itself where eyes will land. They are fast, but they show what is noticed, not how a brain responds. NeuroLens sits in the gap: a predicted brain response for every second, with a second pass that removes the sound, for a dollar.

**Slide 5: TRIBE v2** (82 words)

> The model underneath is TRIBE v2, from Meta's AI research lab. It was trained on over a thousand hours of real fMRI from 720 people. It takes three inputs, frames, sound and words, through four models: V-JEPA 2 watches, Wav2Vec-BERT listens, and WhisperX and LLaMA 3.2 handle the speech. Out comes predicted activity at twenty thousand four hundred and eighty-four points on the cortex, every second. In Meta's paper it handles new videos and new people several times better than older models.

**Slide 6: Signals** (99 words)

> We turn those cortex points into seven signals in three groups, using brain areas from the Glasser atlas. Seeing covers faces, bodies, scenes and visual areas. Hearing covers sound. Understanding covers speech and social cues. This is a crop of our live site. The scale is the same on all three, and zero means typical film and TV viewing, so any two videos compare directly. A second pass with the sound removed shows what the soundtrack adds. Hold on to these three groups, because you'll see them move in the demo. Over to my teammate for the business model.


## P2 · Business Model: 416 words, ~2:40

**Slide 7: Business model** (120 words)

> Thanks. NeuroLens is for independent creators and small agencies who make many AI-generated versions of an advert and need to pick one before paying for media. It's pay as you go: one dollar for each started thirty seconds, from prepaid credit. New users get ten dollars once their access request is approved, enough for ten videos. Why credit, not a card per video? A card fee of about 2.9 percent plus thirty cents takes about a third of a one-dollar charge, but only about four percent of a twenty-dollar pack. One caveat: in our proof of concept no one pays real money, and TRIBE v2 is non-commercial, so this is the model we would use if Meta approved a licence.

**Slide 8: Unit economics** (90 words)

> What does one analysis cost us? About 393 seconds of GPU time on an L4 at one dollar twenty-two an hour: thirteen cents. Each session also pays for one GPU start-up and seven idle minutes, shared across its videos. With three videos in a session, a video costs us twenty-three cents. With thirty, fourteen cents. Against a one-dollar price, that is a gross margin of seventy-seven to eighty-six percent, plus about five dollars a month fixed. We hoped for ten cents on Spot GPUs, but Spot capacity was not available.

**Slide 9: Why cloud** (117 words)

> So why a cloud service and not a workstation? A machine with an RTX 4090 costs about five thousand dollars up front. Spread over three years with idle power, that is about one hundred fifty-nine dollars a month before a single video is analysed. Ad testing comes in bursts: an agency may run twenty jobs in an editing sprint, then nothing for days, so that GPU sits idle over eighty percent of the time. The cloud remedy is to rent a GPU only when a video is waiting and stop it after seven idle minutes. Idle, the service costs about five dollars a month. Keeping one L4 warm around the clock would cost about eight hundred ninety.

**Slide 10: SaaS vs on-premise** (89 words)

> So where is the break-even? The owned machine is flat at about one hundred fifty-nine dollars a month, and costs almost nothing per video, about a third of a cent of electricity. AWS has about five dollars a month fixed, but twenty-three cents a video. The lines cross at 670 videos a month. Below that, renting is cheaper. Above it, owning wins: at a thousand videos a month over three years, about fifty-eight hundred dollars owned against eighty-five hundred on AWS. Over to my colleague, who'll show it live.


## P3 · Demo: 493 words, ~3:11

**Slide 11: Demo** (493 words)

> Thanks. Now let me show you the real thing. This is the live site, running on AWS, and the picture on the slide is a link to it. I'll sign in with Google; email works too. My credit balance is at the top. I'll pick a thirty-second clip. Before anything is sent, the app tells me exactly what it will cost: one dollar for each started thirty seconds. I get a short window to cancel, because GPU time costs real money, and then I confirm. The file goes straight from my browser to cloud storage, and the job moves through stages I can watch: checking the file, waiting for a GPU, and analysing. We warmed a GPU up before this session, but even warm, a thirty-second clip takes about seven minutes, so I'll leave it running and open a finished sample: the thirty-second CeraVe ad, one of our free samples. On the left, the video plays with a 3D brain synced to it, and you can drag the brain to rotate it. The orange line is the overall predicted response of an average viewer, compared with typical viewing. Around second six, when the actor holds up the product and looks at the camera, it peaks, and the label says well above typical. The orange dots mark moments the system flags as notable, and the strip of frames underneath shows what was on screen. Click anywhere on the chart and the video jumps to that moment. Now the three groups we just met. Seeing, hearing and understanding sit on the same scale, so you can see which one drives the peak. Here it is seeing and hearing together, the face and the voice, and you can open each group to see its parts. Towards the end the line falls below typical, on the closing product card, with no face and no speech. The toggle shows the same video without sound. That is the second pass I described, and the page tells us forty-six percent of the response comes from the soundtrack. Numbers are useful, but creators want advice. The Report tab is written by an AI model that reads the brain data and the video frames together. It lists strengths and improvements, then specific moments, with the second each one starts, what is on screen, what the brain is doing, and what to change. The Compare tab puts two videos side by side: here, the thirty-second cut against the longer cut of the same ad. The verdict says the short cut's direct opening gives it an early edge, but the two finish close. You can download the result as a CSV, or the report as a PDF. One last thing people ask: what if someone uploads something that is not a video? I'll try a text file renamed to MP4. It is rejected within seconds, before any GPU starts, and nothing is charged. Over to my colleague, who will show how we evaluated it.


## P4 · Evaluation: 483 words, ~3:06

**Slide 12: Validation** (104 words)

> Thanks. Now evaluation, organised around the questions a user would ask. First: can we trust it? Two things we checked ourselves. The same clip through the same code on our RTX 4090 and on AWS gives the same result: across all eleven series, no value differs by more than 0.005. And 77 of 80 automated refund tests refunded a failed job exactly once; the other three stopped on a GPU-library error during setup. What we did not check: the model's accuracy is Meta's result, and we did not test whether a higher response means more views or sales, so we make no such claim.

**Slide 13: Wait** (86 words)

> Next: how long will a user wait? Same thirty-second clip each time. On our own RTX 4090 it takes 128 seconds. On AWS with the GPU already warm it takes 415: about three seconds to upload, nineteen waiting in the queue, and the rest analysing. After a quiet spell, the first video takes about 779 seconds, about thirteen minutes, because a GPU has to start: our two cold starts waited 452 and 277 seconds. That is the price of paying for a GPU only when needed.

**Slide 14: Scaling** (74 words)

> Next: what if several people use it at once? We sent five uploads at the same moment. Both setups run one job at a time, so everyone queues. On AWS each block is one person's job, about 323 seconds each, and the last finished after 2,145 seconds, about thirty-six minutes. On our own machine all five were done in 575 seconds. Nothing was dropped. Serving more at once would need a GPU quota increase.

**Slide 15: Usability** (107 words)

> Next, can people use it? Eleven people tried NeuroLens at our booth, a small formative test, and answered the System Usability Scale. The mean is 71.6 out of 100, and five of the eleven reached eighty, which is usually read as very good. Scores split: six people at 77.5 or more, five between 47.5 and 55. One respondent gave a four to every item, which scores as fifty, so the low group may understate usability. Two things confused people: a muted video in the Compare tab, and what the brain regions mean. Both are small fixes. Caveat: eight of the eleven were students, not our target users.

**Slide 16: Value** (112 words)

> Is it worth using? Nine of eleven, 82 percent, said it showed them something they had not noticed. Seven, 64 percent, would pay one dollar for a thirty-second video, and two more might. The chart scored 4.3 out of 5 for understanding the video, and 4.1 for use when editing. Trust was lower, at 3.7: three of the four who would not pay, or were unsure, doubted the accuracy. One person said it helps me improve my video easily and update the scenes to make it more engaging. That is why we are upfront about what we did not validate. Over to my colleague for what we learned and where this goes.


## P5 · Conclusion: 485 words, ~3:08

**Slide 17: Lessons** (84 words)

> Thanks. Let us look back at what we learned. Three things broke. TRIBE v2's speech step attached the whole transcript to every audio chunk, so words repeated; we replaced it so each chunk keeps its own words. GPU memory grows with audio length, so we split audio over sixty seconds into equal pieces. And an edge case we have not fixed: a video arriving just as the seven idle minutes end can still stop the GPU, so it waits five to eight more minutes.

**Slide 18: Limits and ethics** (122 words)

> Next, limits and ethics. NeuroLens gives an average viewer's predicted brain response, a virtual pre-test for editing. It is not a prediction of views or sales, which we did not test. It is not instant, since a cold start waits about six minutes. And it has not been tested on our target users, as eight of our eleven testers were students. On ethics: TRIBE v2 is non-commercial, so a paid launch needs a licence. No real person is scanned or profiled. Still, a tool that shows what holds attention could be used to hold it at any cost, so we position it for checking edits, not manipulating people. Our survey was anonymous and consented, and our report declares how we used AI.

**Slide 19: Where next** (122 words)

> Where does this go next? Our proof of concept runs one GPU at a time. We tested a larger design with CPU instances, then moved the GPU to SageMaker after three findings. Spot did not deliver: all twenty-six launches failed for lack of capacity. A smaller GPU is enough, since the model peaked at about twenty gigabytes. And the GPU sets the speed: an L40S finished a 119 second clip in 491 seconds, against 1,540 on an L4. The larger design adds about seventy dollars a month of fixed cost, so it pays off above roughly a thousand videos a month. At today's volume SageMaker is the better choice, and the first step is to build it and measure its start-up time.

**Slide 20: Conclusion** (100 words)

> To wrap up. NeuroLens returns a viewer's predicted brain response in about seven minutes for about one dollar, where an fMRI study costs ten thousand dollars or more and takes weeks. Below 670 videos a month, renting a GPU costs less than owning one. The trade-off is the wait: after a quiet spell, the first video waits about six minutes. The model predicts an average viewer, not views or sales. Next: a commercial licence, the scaled-up design once built and measured, and small fixes from our usability study. Thank you for listening, and we'd be happy to take your questions.

**Slide 21: Q&amp;A** (57 words)

> Thank you for supporting us at STePS. Open for questions. Keep answers ready on: why this model and its licence, what the prediction does and does not mean, the cloud design and the cold-start wait, why SageMaker rather than Spot or our own GPU pool (the Where next slide), how refunds work, and what the experiments showed.


**Total: 2377 words, ~15:18**
