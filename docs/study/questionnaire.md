# NeuroLens booth survey (Google Form text)

Drop-in booth at STePS (11 Nov, 2:00–6:30 pm), one booth account on a team laptop. Visitors try a sample or upload a short clip, then fill in this form (QR code at the booth, link on the website). Anonymous: the form does not collect email addresses. About 3-4 minutes.

`make_form.gs` (next to this file) builds this form in Google Forms: paste it into a new project at script.google.com, press Run, allow access once; the log prints the form's link and edit link. Keep this file and the script in step.

Form settings: "Collect email addresses" off; "Limit to 1 response" off (that would force a Google sign-in); question 2's rows in the order below, never shuffled (SUS scoring depends on it).

---

**Title:** NeuroLens: quick feedback (about 3 minutes)

**Description:** This survey is anonymous and part of an NUS course project. Your answers appear in our report only as totals and short quotes. Please don't write your name, email or other personal details. Because it is anonymous, an answer can't be withdrawn once it has been submitted.

**0. Consent** (checkbox, required)
- I agree that my answers can be used as described above.

**1. What did you do?** (checkboxes, required)
- Looked at a sample video
- Uploaded a video (my own or one from the booth)
- Found where the brain response peaks or drops
- Compared two videos
- Read the AI report

**2. How much do you agree with each statement?** (grid, required in each row; columns 1 Strongly disagree, 2, 3, 4, 5 Strongly agree)
1. I think that I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I think that I would need the support of a technical person to be able to use this system.
5. I found the various functions in this system were well integrated.
6. I thought there was too much inconsistency in this system.
7. I would imagine that most people would learn to use this system very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going with this system.

**3. How much do you agree?** (grid, required in each row; same columns)
1. The chart helped me understand the video.
2. I trust the predictions.
3. I would use this when editing a video.

**4. Did NeuroLens show you something about the video you hadn't noticed yourself?** (multiple choice, required)
- Yes
- No
- Not sure

**5. If NeuroLens were a paid service, would you pay $1.00 to analyse a 30-second video?** (multiple choice, required)
- Yes
- Maybe
- No

**5b. Why?** (paragraph, optional)

**6. What, if anything, confused you?** (paragraph, optional)

**7. Which describes you best?** (multiple choice, optional)
- Student
- I make videos or social content
- I work in marketing, advertising or media
- Other

---

## Scoring (for the report)

Question 2 is the System Usability Scale (SUS; Brooke, 1996), wording unchanged. Per person: odd rows score (answer - 1), even rows score (5 - answer); the sum times 2.5 gives 0 to 100. About 68 is a typical product. Report the mean, the spread and the number of answers.

Question 3 answers the usefulness and trust question: report each row's median and the share answering 4 or 5. Question 4 is a light, self-reported baseline (the tool against the visitor's own judgement); the report says it is self-reported. Question 1 splits the results by task. Question 5 is hypothetical: the model's licence is non-commercial, so NeuroLens is never sold; report it as a hint of perceived value only. Questions 5b and 6 supply quotes and future work.
