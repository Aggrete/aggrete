# Sample documents

Documents you can feed to the ingest tool to draft a `coc.yaml`:

```bash
aggrete-ingest samples/<file> --domains proxy.config.yaml -o coc.draft.yaml
```

Ingest accepts PDF, DOCX, Markdown and plain text. The draft is verified against
the engine before it is written. Review every drafted rule before using it.

## Files

### `northwind-handbook.docx` (synthetic)

A fictional handbook for "Northwind Systems GmbH", written for this project. Its
section 7 clauses are authored to map onto Aggrete's rule types (combining
personnel, budget and rotation records; minimum group sizes for pay; timesheet
self-comparison), so it is the best document for demonstrating what the proxy
enforces. Not a real company. No licensing restrictions.

### `gsa-tts-code-of-conduct.docx` (real, public domain)

The Code of Conduct from the U.S. General Services Administration, Technology
Transformation Services (TTS) Handbook. As a work of the U.S. Government it is in
the public domain, and TTS additionally dedicates it worldwide under CC0 1.0.

- Source: <https://github.com/GSA-TTS/handbook> (`pages/about-us/code-of-conduct.md`)
- License: Public domain / [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- Note: a real code of conduct, but focused on behavior and anti-harassment.
  It contains few data-access clauses, so ingest drafts softer rules from it than
  from the Northwind sample.

### `indiana-employee-handbook.pdf` (real, public sector)

The State of Indiana Employee Handbook, published by the Indiana State Personnel
Department. Included as an example of a comprehensive real handbook with
confidentiality, conflict-of-interest and personnel-records clauses.

- Source: <https://www.in.gov/spd/files/eehandbook.pdf> (retrieved 2026-08-28)
- License: a work of a U.S. state government, published as a public record and
  generally free to use. It is not an explicit CC0 dedication; it is included
  here unmodified for demonstration, with attribution to the State of Indiana.
