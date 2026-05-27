-- Seed data for reference values of nuclear materials
-- Values sourced from experimental data and standard literature

INSERT INTO reference_values (element, crystal_structure, property_name, value, unit, temperature, source, notes) VALUES
-- Uranium (gamma phase, BCC)
('U', 'BCC', 'lattice_constant', 3.47, 'Å', 1050, 'experimental', 'gamma-U at 1050K'),
('U', 'BCC', 'C11', 125, 'GPa', 0, 'review', 'Estimated value'),
('U', 'BCC', 'C12', 85, 'GPa', 0, 'review', 'Estimated value'),
('U', 'BCC', 'C44', 45, 'GPa', 0, 'review', 'Estimated value'),
('U', 'BCC', 'bulk_modulus', 113, 'GPa', 0, 'experimental', 'gamma-U'),
('U', 'BCC', 'vacancy_formation_energy', 2.0, 'eV', 0, 'DFT', 'U metal estimated'),
('U', 'BCC', 'melting_point', 1405, 'K', 0, 'experimental', 'Standard melting point'),

-- Uranium (alpha phase, Orthorhombic)
('U', 'orthorhombic', 'lattice_constant_a', 2.85, 'Å', 300, 'experimental', 'alpha-U a-parameter'),
('U', 'orthorhombic', 'lattice_constant_b', 5.87, 'Å', 300, 'experimental', 'alpha-U b-parameter'),
('U', 'orthorhombic', 'lattice_constant_c', 4.95, 'Å', 300, 'experimental', 'alpha-U c-parameter'),

-- Molybdenum (BCC)
('Mo', 'BCC', 'lattice_constant', 3.147, 'Å', 0, 'experimental', 'Standard Mo BCC'),
('Mo', 'BCC', 'C11', 463, 'GPa', 0, 'experimental', 'Elastic constant'),
('Mo', 'BCC', 'C12', 161, 'GPa', 0, 'experimental', 'Elastic constant'),
('Mo', 'BCC', 'C44', 109, 'GPa', 0, 'experimental', 'Elastic constant'),
('Mo', 'BCC', 'bulk_modulus', 262, 'GPa', 0, 'experimental', 'Standard bulk modulus'),
('Mo', 'BCC', 'vacancy_formation_energy', 3.0, 'eV', 0, 'experimental', 'Formation energy'),
('Mo', 'BCC', 'surface_energy_110', 3.34, 'J/m²', 0, 'experimental', '110 surface'),

-- Zirconium (HCP phase, RT)
('Zr', 'HCP', 'lattice_constant_a', 3.232, 'Å', 300, 'experimental', 'Zirconium alpha'),
('Zr', 'HCP', 'lattice_constant_c', 5.147, 'Å', 300, 'experimental', 'Zirconium alpha'),
('Zr', 'HCP', 'C11', 143, 'GPa', 0, 'experimental', 'Elastic constant'),
('Zr', 'HCP', 'C12', 73, 'GPa', 0, 'experimental', 'Elastic constant'),
('Zr', 'HCP', 'C13', 67, 'GPa', 0, 'experimental', 'Elastic constant'),
('Zr', 'HCP', 'C33', 165, 'GPa', 0, 'experimental', 'Elastic constant'),
('Zr', 'HCP', 'C44', 35, 'GPa', 0, 'experimental', 'Elastic constant'),
('Zr', 'HCP', 'bulk_modulus', 97, 'GPa', 0, 'experimental', 'Standard Zr HCP'),
('Zr', 'HCP', 'vacancy_formation_energy', 1.7, 'eV', 0, 'experimental', 'HCP Zr'),

-- Zirconium (beta phase, BCC)
('Zr', 'BCC', 'lattice_constant', 3.609, 'Å', 1136, 'experimental', 'beta-Zr at 1136K'),

-- U-Mo alloy (gamma phase, BCC U-10Mo)
('U-10Mo', 'BCC', 'lattice_constant', 3.38, 'Å', 0, 'experimental', 'U-10Mo BCC'),
('U-10Mo', 'BCC', 'bulk_modulus', 130, 'GPa', 0, 'review', 'Estimated'),

-- U-Zr alloy (gamma phase, BCC)
('U-Zr', 'BCC', 'lattice_constant', 3.50, 'Å', 0, 'review', 'Composition dependent average'),

-- Iron (BCC)
('Fe', 'BCC', 'lattice_constant', 2.870, 'Å', 0, 'experimental', 'Alpha iron'),
('Fe', 'BCC', 'C11', 231, 'GPa', 0, 'experimental', 'Elastic constant'),
('Fe', 'BCC', 'C12', 135, 'GPa', 0, 'experimental', 'Elastic constant'),
('Fe', 'BCC', 'C44', 116, 'GPa', 0, 'experimental', 'Elastic constant'),
('Fe', 'BCC', 'bulk_modulus', 167, 'GPa', 0, 'experimental', 'Standardbulk modulus'),
('Fe', 'BCC', 'vacancy_formation_energy', 1.72, 'eV', 0, 'experimental', 'Vacancy energy'),

-- Uranium Dioxide (Fluorite structure)
('UO2', 'fluorite', 'lattice_constant', 5.470, 'Å', 0, 'experimental', 'UO2 rutile/fluorite'),
('UO2', 'fluorite', 'C11', 390, 'GPa', 0, 'experimental', 'Elastic constant'),
('UO2', 'fluorite', 'C12', 117, 'GPa', 0, 'experimental', 'Elastic constant'),
('UO2', 'fluorite', 'C44', 62, 'GPa', 0, 'experimental', 'Elastic constant'),
('UO2', 'fluorite', 'bulk_modulus', 211, 'GPa', 0, 'experimental', 'UO2 Bulk'),

-- Niobium (BCC)
('Nb', 'BCC', 'lattice_constant', 3.300, 'Å', 0, 'experimental', 'Niobium BCC'),
('Nb', 'BCC', 'C11', 246, 'GPa', 0, 'experimental', 'Elastic constant'),
('Nb', 'BCC', 'C12', 134, 'GPa', 0, 'experimental', 'Elastic constant'),
('Nb', 'BCC', 'C44', 29, 'GPa', 0, 'experimental', 'Elastic constant'),
('Nb', 'BCC', 'bulk_modulus', 171, 'GPa', 0, 'experimental', 'Niobium bulk');
