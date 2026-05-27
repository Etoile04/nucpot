# Technical Reuse Strategy: NucPot Verification Pipeline

## Strategic Reuse
To accelerate development, this project will leverage components from the `nucpot-autovc` project:
- **Grading Engine**: Logic for comparing calculated values against reference data.
- **Calculator Patterns**: Abstracted patterns for initializing ASE calculators.
- **Testing Fixtures**: Pre-defined atomic structures for verification benchmarks.

## Key Technical Divergences
Unlike `nucpot-autovc`, the current verification pipeline implements several architectural changes:
- **Persistence**: Replaces SQLAlchemy with **Supabase** for managed database operations.
- **Communication**: Uses the `httpx` REST client for asynchronous API communication.
- **Deployment**: Simplified containerization via a dedicated `verify-service` and Docker Compose.

## Technical Implementation details

### Calculator Selection Strategy
The pipeline will employ a strategy pattern to select the appropriate calculator based on the potential type:
- `EAM` $\rightarrow$ `ase.calculators.eam`
- `LJ` $\rightarrow$ `ase.calculators.lj`
- `Complex/ML` $\rightarrow$ LAMMPS Backend

### Accuracy Estimates
Property calculations will be validated against a threshold of acceptable variance ($\epsilon$) relative to reference data. Estimates for accuracy depend on the potential fidelity (Classical $\approx$ 5-10%, ML $\approx$ 1-2%).

### Reference Data Sources
Verification results will be benchmarked against:
- **NIST IPR**: Interatomic Potential Repository.
- **OpenKIM**: Knowledgebase for Interatomic Models.
- **Experimental Literature**: Peer-reviewed data for lattice and elastic constants.
