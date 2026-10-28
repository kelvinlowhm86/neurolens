"""The report's request to Claude (M3g §5b). Pure Python: the Lambda and the tests import it."""

import base64
import json

from neurolens import results

# The answer's limits (M3g §5d): checked by the report Lambda; the counts are also the prompt's.
MAX_HEADLINE = 120
MAX_SUMMARY = 900
MIN_MOMENTS, MAX_MOMENTS = 1, 8
MAX_LISTS = 4
# The model counts characters only roughly (it counts items exactly), so the prompt asks for
# shorter text than the check accepts: a slight overshoot must not throw away a paid report.
ASK_HEADLINE = 100
ASK_SUMMARY = 700

SYSTEM_PROMPT = """You write a short report about a video for the person who made it. You are
given the predicted brain response for an average viewer, second by second, and one picture from
each second of the video.

The seven signals are: faces, bodies and scenes (the visual areas that respond to those things),
visual (the wider visual response), sound (the hearing response), speech and social (the language
and social response), and overall. They belong to three groups: see (faces, bodies, scenes,
visual), hear (sound) and understand (speech, social).

Every value is a signed mean, not an absolute measurement, and 0 is typical film or television
viewing on the model's own scale. Use the values to compare this video's moments and to compare
it with other videos, never as a measure of how engaged or how good it is. These are model
predictions of an average viewer's brain, not measurements of real people.

Write for a video maker, in plain words. Cite times in whole seconds. Use only the spoken words
you are given, and never guess what was said beyond them. Name what happens on screen from the
pictures. Say clearly when the brain data and what happens on screen do not agree.

Limits: the headline at most {headline} characters, the summary at most {summary} characters,
{min_moments} to {max_moments} moments, at most {lists} strengths and {lists} improvements.
Each moment's start and end are whole seconds from 0 to the video's last second, start <= end.
""".format(
    headline=ASK_HEADLINE,
    summary=ASK_SUMMARY,
    min_moments=MIN_MOMENTS,
    max_moments=MAX_MOMENTS,
    lists=MAX_LISTS,
)


def build_request(result, frames, words, summary):
    """(system, content) for the report Lambda: the numbers and summary, the words, then each
    second's label and picture (M3g §5b)."""
    signals = result["signals"]
    data = {
        "duration_seconds": result["duration_seconds"],
        "signals": {
            name: [round(float(value), 2) for value in signals[name]] for name in results.SERIES
        },
        "muted_overall": [round(float(value), 2) for value in result["muted"]["overall"]],
        "summary": summary,
    }
    content = [{"type": "text", "text": json.dumps(data)}]
    if words:
        lines = "\n".join(f"{word['start']:.2f} s: {word['text']}" for word in words)
        content.append({"type": "text", "text": f"Spoken words:\n{lines}"})
    for second, frame in enumerate(frames[: result["duration_seconds"]]):
        content.append({"type": "text", "text": f"t = {second} s"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(frame).decode(),
                },
            }
        )
    return SYSTEM_PROMPT, content
