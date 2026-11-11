#import "@reader/merman:0.5.0": show-mermaid-blocks

#set document(title: sys.inputs.title)
#set page(paper: "a4", fill: white, margin: (x: 18mm, y: 20mm), numbering: "1")
#set text(font: "Libertinus Serif", size: 11pt, lang: "en")
#set par(justify: true, leading: 0.65em)
#set heading(numbering: none)
#set raw(tab-size: 4)
#show raw: set text(font: "DejaVu Sans Mono", size: 9pt)
#set table(inset: 6pt, stroke: 0.4pt + rgb("#d9dfda"))
#show link: set text(fill: rgb("#006d68"))
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  text(size: 18pt, weight: "bold", fill: rgb("#006d68"), it)
}
#show raw.where(block: true): it => block(
  fill: rgb("#f2f4f5"), inset: 10pt, radius: 4pt, width: 100%,
  breakable: true, it,
)
#show raw.where(lang: "mermaid"): show-mermaid-blocks(
  width: 100%, error-mode: "panic",
  site-config: (layout: "dagre", theme: "default", look: "classic"),
)
#let horizontalRule = line(start: (25%, 0%), end: (75%, 0%))

#align(center)[
  #text(size: 24pt, weight: "bold", sys.inputs.title)

  Reading notes
]
#v(12mm)
#outline(title: [Contents], depth: 2)
#pagebreak(weak: true)

