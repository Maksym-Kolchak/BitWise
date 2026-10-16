# UI implementation plan
Preserve the existing Bitwise identity; this is workflow integration, not redesign.
- White #FFFFFF: page and form surfaces.
- Lilac #F7F4FB: selected workspace and supporting notes.
- Black #000000: primary text.
- Purple #7D39EB: links, focus and workspace identity.
- Lime #C6FF33: primary actions; keep black text.
- Muted #5B5466: explanatory text.
Typography stays Manrope (body) / Unbounded (headings), with system fallbacks.
Use existing container/card/field/button patterns. Company name and role stay visible
above company tools, not just in a closed avatar menu. Team controls stack on mobile.
Separate personal account, company settings, invitation inbox, and course assignments.
No decorative graphics or animation; focus, labels, and reduced motion matter more.
Review: information is specific to a course-authoring workflow, no generic dashboard
widgets added. Reuse existing styles; add only responsive workspace controls.
