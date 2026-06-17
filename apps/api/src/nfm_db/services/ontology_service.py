"""Ontology service for NVL visualization data."""

from nfm_db.schemas.viz import Node, NvlResponse, Relationship, VizStatsResponse

# Sample ontology data for demonstration
SAMPLE_NODES = [
    Node(
        id="metal-uranium",
        name="Uranium",
        classes=["Element", "Metal", "Actinide"],
        properties={"atomic_number": "92", "symbol": "U"},
    ),
    Node(
        id="metal-plutonium",
        name="Plutonium",
        classes=["Element", "Metal", "Actinide"],
        properties={"atomic_number": "94", "symbol": "Pu"},
    ),
    Node(
        id="compound-uo2",
        name="Uranium Dioxide",
        classes=["Compound", "Oxide", "NuclearMaterial"],
        properties={"formula": "UO2", "use": "Fuel"},
    ),
    Node(
        id="property-density",
        name="Density",
        classes=["Property"],
        properties={"unit": "g/cm³", "type": "Physical"},
    ),
]

SAMPLE_RELATIONSHIPS = [
    Relationship(
        id="rel-1",
        source="metal-uranium",
        target="compound-uo2",
        type="COMPOSES",
    ),
    Relationship(
        id="rel-2",
        source="metal-plutonium",
        target="compound-uo2",
        type="COMPOSES",
    ),
    Relationship(
        id="rel-3",
        source="compound-uo2",
        target="property-density",
        type="HAS_PROPERTY",
    ),
]


async def get_nvl_data(
    class_filter: str | None = None,
    search_term: str | None = None,
    max_nodes: int | None = None,
) -> NvlResponse:
    """Get NVL data with optional filtering.

    Args:
        class_filter: Filter nodes by class subtree
        search_term: Filter nodes by search term in name
        max_nodes: Limit number of nodes returned

    Returns:
        NvlResponse with filtered nodes and relationships
    """
    # Start with all nodes
    nodes = list(SAMPLE_NODES)

    # Apply class filter
    if class_filter:
        nodes = [n for n in nodes if class_filter in n.classes]

    # Apply search filter
    if search_term:
        search_lower = search_term.lower()
        nodes = [n for n in nodes if search_lower in n.name.lower()]

    # Apply max_nodes limit
    if max_nodes and len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]

    # Get relationships for filtered nodes
    node_ids = {n.id for n in nodes}
    relationships = [
        r for r in SAMPLE_RELATIONSHIPS
        if r.source in node_ids and r.target in node_ids
    ]

    return NvlResponse(nodes=nodes, relationships=relationships)


async def get_viz_stats() -> VizStatsResponse:
    """Get ontology statistics.

    Returns:
        VizStatsResponse with total counts and class distribution
    """
    class_counts: dict[str, int] = {}
    for node in SAMPLE_NODES:
        for cls in node.classes:
            class_counts[cls] = class_counts.get(cls, 0) + 1

    return VizStatsResponse(
        total_nodes=len(SAMPLE_NODES),
        total_relationships=len(SAMPLE_RELATIONSHIPS),
        class_counts=class_counts,
    )
