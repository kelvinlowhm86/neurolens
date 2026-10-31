// Builds the NeuroLens booth survey (questionnaire.md) in Google Forms.
// Paste into a new project at script.google.com, press Run, allow access once.
// Each run makes a NEW form; the log prints its links.

function makeForm() {
  var agree = ['1 Strongly disagree', '2', '3', '4', '5 Strongly agree'];

  var form = FormApp.create('NeuroLens: quick feedback (about 3 minutes)');
  form.setDescription(
    'This survey is anonymous and part of an NUS course project. Your answers appear in our ' +
    'report only as totals and short quotes. Please don\'t write your name, email or other ' +
    'personal details. Because it is anonymous, an answer can\'t be withdrawn once it has been submitted.');
  form.setCollectEmail(false);
  form.setLimitOneResponsePerUser(false);

  form.addCheckboxItem()
    .setTitle('Consent')
    .setChoiceValues(['I agree that my answers can be used as described above.'])
    .setRequired(true);

  form.addCheckboxItem()
    .setTitle('What did you do?')
    .setChoiceValues([
      'Looked at a sample video',
      'Uploaded a video (my own or one from the booth)',
      'Found where the brain response peaks or drops',
      'Compared two ads',
      'Read the AI report',
    ])
    .setRequired(true);

  // System Usability Scale (Brooke, 1996): wording and order unchanged.
  form.addGridItem()
    .setTitle('How much do you agree with each statement?')
    .setRows([
      'I think that I would like to use this system frequently.',
      'I found the system unnecessarily complex.',
      'I thought the system was easy to use.',
      'I think that I would need the support of a technical person to be able to use this system.',
      'I found the various functions in this system were well integrated.',
      'I thought there was too much inconsistency in this system.',
      'I would imagine that most people would learn to use this system very quickly.',
      'I found the system very cumbersome to use.',
      'I felt very confident using the system.',
      'I needed to learn a lot of things before I could get going with this system.',
    ])
    .setColumns(agree)
    .setRequired(true);

  form.addGridItem()
    .setTitle('How much do you agree?')
    .setRows([
      'The chart helped me understand the ad.',
      'I trust the predictions.',
      'I would use this when editing an ad.',
    ])
    .setColumns(agree)
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle('Did NeuroLens show you something about the ad you hadn\'t noticed yourself?')
    .setChoiceValues(['Yes', 'No', 'Not sure'])
    .setRequired(true);

  form.addMultipleChoiceItem()
    .setTitle('Would you pay $1.00 to analyse a 30-second ad?')
    .setChoiceValues(['Yes', 'Maybe', 'No'])
    .setRequired(true);

  form.addParagraphTextItem().setTitle('Why?');
  form.addParagraphTextItem().setTitle('What, if anything, confused you?');

  form.addMultipleChoiceItem()
    .setTitle('Which describes you best?')
    .setChoiceValues(['Student', 'I work in marketing, advertising or media', 'Other']);

  Logger.log('Share this link: ' + form.getPublishedUrl());
  Logger.log('Edit the form here: ' + form.getEditUrl());
}
