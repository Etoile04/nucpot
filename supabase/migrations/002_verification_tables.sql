-- Verification pipeline tables

-- Reference values for nuclear materials (experimental/DFT)
CREATE TABLE reference_values (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  element         VARCHAR(8) NOT NULL,         -- U, Mo, Zr, Fe, Nb, etc.
  crystal_structure VARCHAR(16) NOT NULL,       -- BCC, FCC, HCP, orthorhombic
  property_name   VARCHAR(64) NOT NULL,         -- lattice_constant, C11, C12, C44, bulk_modulus, vacancy_formation_energy, surface_energy_110, etc.
  value           DOUBLE PRECISION NOT NULL,
  unit            VARCHAR(16) NOT NULL,         -- Å, GPa, eV, J/m², K
  temperature     DOUBLE PRECISION DEFAULT 0,   -- K (0 = 0K calculation)
  source          VARCHAR(128),                 -- experimental, DFT, review
  reference_doi   TEXT,
  notes           TEXT,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(element, crystal_structure, property_name, temperature)
);

-- Verification jobs
CREATE TABLE verifications (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  potential_id    UUID NOT NULL REFERENCES potentials(id) ON DELETE CASCADE,
  status          VARCHAR(16) DEFAULT 'pending',  -- pending, running, completed, failed
  properties_requested TEXT[] NOT NULL,            -- list of property names to compute
  results         JSONB DEFAULT '{}',              -- computed property results
  grades          JSONB DEFAULT '{}',              -- per-property grades
  overall_grade   VARCHAR(2),                      -- A, B, C, D, F
  error_message   TEXT,
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  compute_time_ms INTEGER                          -- wall-clock time in ms
);

-- Indexes
CREATE INDEX idx_reference_values_lookup ON reference_values(element, crystal_structure);
CREATE INDEX idx_verifications_potential ON verifications(potential_id);
CREATE INDEX idx_verifications_status ON verifications(status);

-- RLS
ALTER TABLE reference_values ENABLE ROW LEVEL SECURITY;
ALTER TABLE verifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Reference values viewable by all" ON reference_values FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Service role manage reference values" ON reference_values FOR ALL TO service_role USING (true);

CREATE POLICY "Verifications viewable by all" ON verifications FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Service role manage verifications" ON verifications FOR ALL TO service_role USING (true);
