# Literature Search API Endpoints

## NIST IPR (Interatomic Potentials Repository)

### Base URL
- **Website**: https://www.ctcms.nist.gov/potentials/
- **API**: RESTful interface for programmatic access
- **Documentation**: https://potentials.nist.gov/api.html

### Search Endpoints

#### Search by Element
```
GET https://potentials.nist.gov/potentials/element/{symbol}
```
- **Parameters**:
  - `symbol`: Element symbol (U, Zr, Fe, O, etc.)
- **Returns**: List of potentials for specified element
- **Example**: `GET /potentials/element/U`

#### Search by Material
```
GET https://potentials.nist.gov/potentials/material/{formula}
```
- **Parameters**:
  - `formula`: Chemical formula (UO2, UZr, etc.)
- **Returns**: Potentials for compound/alloy
- **Example**: `GET /potentials/material/UO2`

#### Search by Potential Type
```
GET https://potentials.nist.gov/potentials/type/{potential_type}
```
- **Parameters**:
  - `potential_type`: EAM, Buckingham, Tersoff, etc.
- **Returns**: All potentials of specified type
- **Example**: `GET /potentials/type/EAM`

### Download Endpoints

#### Download Potential File
```
GET https://potentials.nist.gov/download/{potential_id}
```
- **Parameters**:
  - `potential_id`: Unique identifier from search
- **Returns**: Potential file (lammps format)
- **Example**: `GET /download/U_u3.eam`

### Metadata Endpoints

#### Get Potential Details
```
GET https://potentials.nist.gov/potentials/{potential_id}
```
- **Returns**: Full metadata including:
  - Publication citation
  - Fitted properties
  - Temperature range
  - Supported elements
  - Accuracy metrics

---

## OpenKIM (Open Knowledgebase of Interatomic Models)

### Base URL
- **Website**: https://openkim.org/
- **API**: RESTful API with MongoDB query syntax
- **Documentation**: https://openkim.org/doc/

### Search Endpoints

#### Search Models
```
POST https://query.openkim.org/api/models
```
- **Body**: MongoDB-style query
- **Example Query**:
```json
{
  "kim_elements": "U",
  "extended_model_type": "EAM"
}
```

#### Search by Property
```
POST https://query.openkim.org/api/runner.materials
```
- **Body**: Property and element query
- **Returns**: Models with computed properties
- **Example**:
```json
{
  "elements": ["U"],
  "property-name": "structure-cubic-crystal-lattice-constant"
}
```

### Verification Endpoints

#### Get Verification Results
```
GET https://openkim.org/verifications/{model_id}
```
- **Returns**: Verification against test cases
- **Includes**:
  - Property predictions vs. reference
  - Error metrics
  - Benchmark results

---

## Materials Project

### Base URL
- **Website**: https://materialsproject.org/
- **API**: RESTful with API key required
- **Documentation**: https://materialsproject.org/docs

### Authentication
```
Header: X-API-Key: {your_api_key}
```

### Search Endpoints

#### Search by Formula
```
GET https://api.materialsproject.org/materials/{formula}/summary
```
- **Parameters**:
  - `formula`: Chemical formula (UO2, Zr, etc.)
  - `API_KEY`: Your personal API key
- **Returns**: Material summary with properties
- **Example**: `GET /materials/UO2/summary`

#### Get Structure
```
GET https://api.materialsproject.org/materials/{material_id}/structure
```
- **Returns**: Crystal structure information
  - Lattice parameters
  - Space group
  - Atomic positions
  - Formation energy

#### Get Properties
```
GET https://api.materialsproject.org/materials/{material_id}/properties
```
- **Returns**: Computed properties:
  - Elastic moduli
  - Band structure
  - Phonon properties
  - Surface energies
  - Thermal expansion

### Endpoints by Property Type

#### Elastic Constants
```
GET https://api.materialsproject.org/materials/elasticity/{material_id}
```
- **Returns**: Elastic tensor, bulk modulus, shear modulus

#### Thermodynamics
```
GET https://api.materialsproject.org/materials/thermo/{material_id}
```
- **Returns**:
  - Formation energy
  - Energy above hull
  - Phase stability

#### Phonons
```
GET https://api.materialsproject.org/materials/phonons/{material_id}
```
- **Returns**: Vibrational properties, Debye temperature

---

## ICSD (Inorganic Crystal Structure Database)

### Access
- **Website**: https://icsd.fiz-karlsruhe.de/
- **Note**: Requires institutional subscription
- **Alternative**: Use Materials Project for open-access crystal data

### Query Format
- Web interface only (no public API)
- Export to CIF for LAMMPS conversion
- Use atomsk orVESTA for format conversion

---

## General Literature Databases

### Web of Science
- **URL**: https://www.webofscience.com/
- **Query**: Material + property keywords
- **Filter**: By year, journal, document type
- **Export**: BibTeX, EndNote formats

### PubMed (for biological materials)
- **URL**: https://pubmed.ncbi.nlm.nih.gov/
- **API**: Entrez Programming Utilities
- **Query**: MeSH terms + keywords

### arXiv (preprints)
- **URL**: https://arxiv.org/
- **API**: https://arxiv.org/help/api/
- **Query**: Condensed matter, materials science
- **Note**: Lower credibility (Tier 3) until peer-reviewed

---

## Search Workflow Examples

### Example 1: Find UO₂ Lattice Constant
```bash
# Step 1: Check Materials Project
curl -H "X-API-Key: $MP_API_KEY" \
  "https://api.materialsproject.org/materials/UO2/summary"

# Step 2: If not found, search NIST IPR
curl "https://potentials.nist.gov/potentials/material/UO2"

# Step 3: Search OpenKIM for potentials
curl -X POST "https://query.openkim.org/api/models" \
  -d '{"kim_elements": ["U", "O"]}'

# Step 4: Search general literature (Web of Science)
# Use web interface or API with query: "UO2 lattice constant"
```

### Example 2: Find U-Zr Alloy Potential
```bash
# Step 1: Search NIST IPR for U-Zr
curl "https://potentials.nist.gov/potentials/material/UZr"

# Step 2: Search OpenKIM
curl -X POST "https://query.openkim.org/api/models" \
  -d '{"kim_elements": ["U", "Zr"]}'

# Step 3: Search Materials Project for formation energies
curl -H "X-API-Key: $MP_API_KEY" \
  "https://api.materialsproject.org/materials/U-Zr/summary"

# Step 4: Check literature for specific potentials
# Search: "U-Zr EAM potential" or "U-Zr interatomic potential"
```

### Example 3: Find Elastic Moduli
```bash
# Step 1: Materials Project elastic constants
curl -H "X-API-Key: $MP_API_KEY" \
  "https://api.materialsproject.org/materials/elasticity/{material_id}"

# Step 2: OpenKIM verification results
curl "https://openkim.org/verifications/{model_id}"

# Step 3: Search literature for experimental measurements
# Web of Science: "U elastic constants" OR "UO2 elastic modulus"
```

---

## API Response Formats

### NIST IPR Response (JSON)
```json
{
  "potentials": [
    {
      "id": "U_u3.eam",
      "name": "U EAM potential (u3)",
      "type": "EAM",
      "elements": ["U"],
      "citation": "Author et al., Phys. Rev. B (2015)",
      "temperature_range": "0-1500 K",
      "fitted_properties": ["lattice", "elastic", "vacancy"],
      "download_url": "https://potentials.nist.gov/download/U_u3.eam"
    }
  ]
}
```

### Materials Project Response (JSON)
```json
{
  "material_id": "mp-123456",
  "formula": "UO2",
  "formation_energy_per_atom": -3.5,
  "elastic_tensor": [[...]],
  "lattice_constant": 5.47,
  "space_group": "Fm-3m"
}
```

### OpenKIM Response (JSON)
```json
{
  "models": [
    {
      "kim_code": "EAM_CandidateFunction_U_U_Zr",
      "extended_model_type": "EAM",
      "kim_elements": ["U", "Zr"],
      "publication_year": 2020,
      "verification_results": {
        "lattice_constant_error": 0.02,
        "elastic_modulus_error": 0.15
      }
    }
  ]
}
```

---

## Error Handling

### Common API Errors

#### 404 Not Found
- **Cause**: Material/potential not in database
- **Action**: Try alternative source or database
- **Example**: UO₂ may not be in NIST IPR (use Materials Project)

#### 401 Unauthorized
- **Cause**: Missing or invalid API key
- **Action**: Verify API key, request new key
- **Applies to**: Materials Project, some commercial APIs

#### 429 Too Many Requests
- **Cause**: Rate limit exceeded
- **Action**: Wait before retry, implement backoff
- **Limits**: Materials Project: ~1000 queries/day

#### 500 Server Error
- **Cause**: Database or API service issue
- **Action**: Retry after delay, check service status

### Timeout Handling
```bash
# Set reasonable timeout for all API calls
curl --max-time 30 [URL]

# Implement retry logic
for attempt in {1..3}; do
  curl -f [URL] && break || sleep 5
done
```

---

## Data Quality Checks

### Verify Results Before Use

#### For Potentials (NIST IPR, OpenKIM):
- [ ] Potential file format compatible with LAMMPS
- [ ] Temperature range includes simulation conditions
- [ ] Elements match your system (U, Zr, O, Fe)
- [ ] Verification results available (OpenKIM)
- [ ] Citation available for attribution

#### For DFT Data (Materials Project):
- [ ] Formation energy reasonable
- [ ] Lattice parameters match literature
- [ ] Elastic properties positive definite
- [ ] Band gap appropriate (for semiconductors)
- [ ] Calculation method documented (DFT functional, k-points)

#### For Experimental Data (Literature):
- [ ] Measurement conditions documented
- [ ] Uncertainty reported
- [ ] Sample purity specified
- [ ] Peer-reviewed publication
- [ ] Recent publication (within 20 years) or classic result

---

## Usage Guidelines

### Rate Limiting
- **NIST IPR**: No strict limit, be courteous
- **OpenKIM**: 100 queries/minute recommended
- **Materials Project**: Check dashboard for current limits
- **General**: Implement exponential backoff on failures

### Caching
- Cache results locally to avoid repeated queries
- Use `ETag` headers for conditional requests
- Cache duration: 1 day for stable data, 1 hour for rapidly changing

### Attribution
- Always cite source databases
- Include potential IDs or material IDs
- Acknowledge computational resources

### API Key Management
```bash
# Store API keys in environment variables
export MATERIALS_PROJECT_API_KEY="your_key_here"
export NIST_IPR_API_KEY="your_key_here"  # if required

# Use in scripts
curl -H "X-API-Key: $MATERIALS_PROJECT_API_KEY" [URL]
```

---

## Example Scripts

### Python Example: Search Materials Project
```python
import requests

API_KEY = "your_api_key"
material = "UO2"

url = f"https://api.materialsproject.org/materials/{material}/summary"
headers = {"X-API-Key": API_KEY}

response = requests.get(url, headers=headers)
data = response.json()

print(f"Material ID: {data['material_id']}")
print(f"Formation energy: {data['formation_energy_per_atom']} eV/atom")
```

### Bash Example: Search NIST IPR
```bash
ELEMENT="U"
curl -s "https://potentials.nist.gov/potentials/element/$ELEMENT" | \
  python3 -m json.tool | \
  grep -A 5 '"name"'
```

### Python Example: Search OpenKIM
```python
import requests

query = {
    "kim_elements": ["U", "Zr"],
    "extended_model_type": "EAM"
}

response = requests.post(
    "https://query.openkim.org/api/models",
    json=query
)

models = response.json()
for model in models.get('models', []):
    print(f"Model: {model['kim_code']}")
    print(f"Year: {model.get('publication_year', 'N/A')}")
```

---

## Alternative: Web Scraping (When APIs Unavailable)

### Use Only When:
- No API available
- Public data (no authentication required)
- Low volume (avoid overwhelming servers)

### Tools
- **BeautifulSoup (Python)**: Parse HTML responses
- **Selenium**: Interactive web browsing
- **curl + grep**: Quick extraction from HTML

### Example: Web Scraping (Use Responsibly)
```bash
# Check robots.txt first
curl https://example-site.com/robots.txt

# Simple scraping
curl -s https://example-site.com/data | \
  grep -o "lattice constant.*[0-9.]* Å"
```

### Ethical Guidelines
- Respect robots.txt
- Implement rate limiting (1 request/second minimum)
- Use caching to minimize repeated requests
- Credit data sources
- Consider terms of service
