# NFM-761 Investigation Results

## Current State (2026-07-24)

### ✅ Complete: NVL Portion
- **File**: `apps/web/public/ontology-viewer/data/nvl_ontology_data.json`
  - 927 nodes, 1061 relationships, 172 classes, 755 individuals
- **Pydantic Models**: `apps/api/src/nfm_db/schemas/ontology.py`
  - `OntologyNode` - matches NvlNode structure
  - `OntologyRelationship` - matches NvlRelationship structure  
  - `OntologyStats` - counts for nodes, relationships, classes, individuals
  - `OntologyGraphResponse` - complete envelope (schema_version 1.1)

### ❌ Missing: OntoFuel Portion
- **File**: `material_ontology_enhanced.json` - NOT FOUND in worktree
- **Required Models** (per issue description):
  - `OntologyClass` - name, label, comment, uri, parent_classes, object_properties, data_properties
  - `OntologyIndividual` - name, label, comment, uri, class_type, property_values
  - `ObjectProperty` - name, domain, range, comment
  - `DataProperty` - name, domain, datatype, comment
- **Parser**: No JSON→Python mapping layer for OntoFuel format

## Disposition
The NVL portion is complete. The OntoFuel portion (material_ontology_enhanced.json + models + parser) is missing and was delegated to CPO via NFM-1820.

## Next Steps
- Verify NFM-1820 status
- If NFM-1820 complete, validate OntoFuel models were created
- If NFM-1820 blocked, unblock or reassign
