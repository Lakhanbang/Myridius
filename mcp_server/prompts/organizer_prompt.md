---
name: organizer-prompt
description: Strict guidelines for the Organizer agent to extract information accurately.
---
# RFP Extraction Rules

1. **Extract Verbatim**: Copy numbered/explicit questions exactly. Do NOT infer or invent requirements.
2. **Exclude Rubrics**: IGNORE all 'TARGET RETRIEVAL & VERIFICATION CRITERIA' sections. They are grading rubrics, NOT questions. Only extract actual question sentences (e.g., CP-01:, PD-02:).
3. **JSON Only**: Output a raw JSON array. NO markdown blocks (e.g., ```json), NO `Final Answer:`, just the raw JSON text.

Format:
[
  {"question": "What is the core mission of Myridius?", "extra_info": "Corporate Strategy"}
]
