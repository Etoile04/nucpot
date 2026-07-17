# OpenKIM API Endpoints

Verified 2026-06-19 against live docs at:
- https://openkim.org/doc/usage/kim-query/

## Base URL

```
https://query.openkim.org/api
```

## Endpoints

### List Portable Models
```
POST https://query.openkim.org/api/get_available_models
Content-Type: application/x-www-form-urlencoded (curl compatible)
```

Parameters (all `--data-urlencode`):
- `species=["Al"]` — chemical symbols
- `species_logic=["and"|"or"]` — default "and"
- `model_interface=["mo"|"sm"]` — "mo" = Portable Model, "sm" = Simulator Model
- `potential_type=["eam","meam",...]` — potential type filter
- `simulator_name=["LAMMPS"]` — simulator filter (no effect when model_interface="mo")

Returns: JSON array of KIM ID strings (format `Prefix_AuthorYear_System__MO_<digits>_<version>`).

### Model Detail

**No public JSON endpoint.** Model metadata is published as HTML pages with rich `<meta name="citation_*">` tags.

```
GET https://openkim.org/id/<KIM_LONG_ID>
```

Extractable metadata:
- `<meta name="citation_title">` — full model title
- `<meta name="citation_author">` — one author per tag
- `<meta name="description">` — abstract/description
- `<meta name="citation_doi">` — DOI
- `<meta name="citation_publication_date">` — publication date
- `<meta name="citation_abstract_html_url">` — canonical citation URL
- `<meta name="citation_keywords">` — semicolon-delimited keywords

Species, potential type, and KIM ID are parsed from the long name convention:
`<Prefix>_<Authors>_<Year>_<Elements>__MO_<digits>_<version>`

### Test Results (property query)
```
POST https://query.openkim.org/api/get_test_result
```
Returns property values for a given model + test combination.

## Default Configuration

- `OPENKIM_API_BASE`: `https://query.openkim.org/api` (env-var override)
- `OPENKIM_CACHE_TTL_SECONDS`: `300` (default)
- Timeout: 5.0 seconds per request
