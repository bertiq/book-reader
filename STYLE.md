# Writing and presentation

## Write to teach

Use simple English and a calm, direct style. Explain the subject as a short textbook would. Use technical terms when needed, then explain them in plain words. Prefer connected paragraphs over long lists of labels.

Start with the main idea. Preserve the important reasoning and conditions. Remove repetition, promotion, empty claims, and needless jargon. Avoid phrases such as "the author says", "this chapter discusses", "the book is wrong", and "I am adding". Do not write a book review or a correction report.

Use the current source part as the boundary. Use its examples, formulas, arguments, and code where useful. Clarify or correct them when needed. Do not add unrelated examples, extra formulas, background lessons, shopping advice, or later topics. If an essential prerequisite is missing, give only the minimum explanation needed to understand the current material. Offer a longer explanation only if asked.

## Keep explanations true

Check questionable facts, equations, assumptions, units, code, and terms. Use targeted primary sources when necessary. Do not research every sentence or chase a claim you will omit because it is irrelevant. Correct errors directly in the teaching text, without announcing them. Keep genuine uncertainty, disputed interpretations, and conditions visible. Never turn an opinion into an established fact.

Do not claim code was run, a result reproduced, or a fact verified unless it was. Do not install a book's dependencies or execute downloaded code merely to summarize it. Check small self-contained examples only when it helps confirm a material point. Keep tests and research notes out of the lesson. Use short source footnotes only when useful; there is no separate verification directory.

## Match the material

Use flexible structure rather than forcing identical headings into every part. Explain concepts in prose. Use tables for genuine comparisons. For a mathematical part, show the main equations, explain each symbol, and state assumptions and units. For a programming part, keep the important code and explain what it does.

- Mark code fences with the correct language, such as `python`, `bash`, or `sql`.
- Use `$...$` for inline math and `$$...$$` for display math. Do not put equations in code fences.
- Use `mermaid` fences for small diagrams when relationships or order matter. Prefer static SVG or PNG for exact data plots. Keep assets beside `part.md` in the corresponding `parts/PART-ID/` directory and link with the neighboring filename, for example `![Shape](shape.svg)`.
- Keep diagrams within the current topic. Do not add a graph just to decorate a page.
- Never use ASCII art when a rendered diagram or table is clearer.
- Use accessible colors, readable code, and equations that fit the page. Preserve useful code indentation.
- Avoid raw HTML in lesson Markdown. The reading view handles styling.

End every part with exactly two short, open-ended comprehension questions. Ask about the ideas just taught, including a reason or application when the source supports it. Both questions must be answerable from the part. Do not put their correct answers in the lesson.

## Use book figures to explain

Include an original figure from the current book part when it is necessary to understand a concept or conveys important visual information that prose alone would lose. This can include a spatial relationship, a process, an experimental result, or a worked example. Choose figures for their teaching value; omit decorative or redundant images.

Place the figure beside the relevant explanation and tell the reader what to notice. Give it meaningful alt text and a short caption identifying the source PDF page and the book's figure number when available. Extract the original image or crop the relevant part of the page, preserving readable labels, axes, units, legends, and other context needed to interpret it. Save the asset beside `part.md` in the corresponding `parts/PART-ID/` directory and link it with a relative Markdown image path. Stay within the current part's source boundaries.

Use an original book figure when its specific visual content matters. A generated diagram can clarify relationships, but must not be presented as an image from the book. If a necessary figure cannot be extracted with the available tools, state the limitation briefly and explain its important content in words.

## Saved form

The helper adds the part title, question IDs, and source page note. The agent supplies the lesson body and question text. Show the saved lesson in chat without rewriting it into another version.

The correct answer saved in `answers.md` must stand alone. It must not include "you said", a score, grading, the submitted answer, or attempt history.

The cumulative summary teaches the main ideas in the saved parts. Join related ideas, retain important limits and equations, and remove repeated explanations. Do not add material from unread parts. Do not include unanswered question lists or grading.
