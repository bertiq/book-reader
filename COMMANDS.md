# Reading commands

Open Codex inside one initialized book directory. `prepare` needs no path.

| Prompt | Exact behavior |
| --- | --- |
| `prepare` | Read navigation information, contents, and introductory material only. Create a logical, ordered plan. Pin source identity. Repeating it preserves an existing valid plan and all progress. |
| `next` | Restore saved state, check source identity, and teach exactly the next planned part. Save the part, register two questions, and update continuation context before showing the saved lesson. Cross chapters automatically. |
| `questions` | Show every unanswered question with its permanent ID. Do not show correct answers. Read no PDF. |
| `summary` | Read every currently saved part in bounded batches and rebuild the same `summary.md`. Read no PDF. Do not use the old summary as the only input. |

Commands are case-insensitive and tolerate surrounding whitespace. They are ordinary prompts, not built-in Codex slash commands. Do not invent `next_chapter`, `resume`, `review`, or other reading commands.

## Replies to questions

Immediately after a part, map clearly numbered answers to that part's questions. A single reply can answer one or both questions. If it is unclear which question a reply addresses, ask for its ID. Do not guess or silently mark both answered.

An older question can be answered with `answer C03-P02-Q01 <your answer>`. IDs are unique and permanent within the current book. Accept a reference to an already answered question, but replace its correct answer only when an improvement is needed. Do not store attempts.

In chat, explain which parts of the reply are correct or need correction, then give a complete correct answer. Persist only that correct answer, the question, its ID, its part link, and its status in `answers.md`. Never write the submitted reply or grading comments to a file, helper payload, log, or Git commit message. Codex itself may retain chat history under its own settings; this project does not control that history.

An incorrect attempt still receives a correct answer and changes the entry to `answered`. This means an answer has been supplied and assessed; it does not mean the learner has mastered it.

Saying `next` leaves every unresolved question unanswered. There is no quiz gate.

## Edge cases

- Before preparation, `next` explains that `prepare` is needed. It must not guess a reading order or read ahead.
- After the final part, `next` reports that all planned parts are saved. It does not regenerate a lesson or create a summary without a request.
- With no pending questions, `questions` says so.
- With no saved parts, `summary` says there is nothing to summarize and creates no empty summary.
- Missing or changed source blocks new source reading. Existing parts, questions, and summaries remain usable.
- A changed PDF is never accepted by rerunning `prepare`. The user must explicitly choose how to handle a replacement; recommend a new book directory to preserve the existing work.
- Interrupted delivery can leave a saved lesson that was not visible in chat. The saved file is authoritative. Do not create duplicate part files.
