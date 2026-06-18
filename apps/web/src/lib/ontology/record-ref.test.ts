import { describe, it, expect } from "vitest";
import { extractRecordRef, type OntologyGraph } from "./record-ref";

const GRAPH: OntologyGraph = {
  schema_version: "1.1",
  corpus_id: "smirnov2014",
  nodes: [
    {
      id: "mat:UO2",
      type: "individual",
      record_ref: "/materials/UO2?corpus=smirnov2014",
    },
    {
      id: "mat:U",
      type: "individual",
      record_ref: "/materials/U?corpus=smirnov2014",
    },
    { id: "prop:lattice_constant", type: "class", record_ref: null },
    { id: "no_ref_individual", type: "individual", record_ref: null },
  ],
};

describe("extractRecordRef", () => {
  it("returns the record_ref for a material individual", () => {
    expect(extractRecordRef(GRAPH, "mat:UO2")).toBe(
      "/materials/UO2?corpus=smirnov2014",
    );
  });

  it("returns null for a class node (class nodes carry no deep link)", () => {
    expect(extractRecordRef(GRAPH, "prop:lattice_constant")).toBeNull();
  });

  it("returns null for an individual that has no record_ref", () => {
    expect(extractRecordRef(GRAPH, "no_ref_individual")).toBeNull();
  });

  it("returns null when the node id is not in the graph", () => {
    expect(extractRecordRef(GRAPH, "mat:Pu")).toBeNull();
  });

  it("returns null for an empty or missing nodes array", () => {
    expect(extractRecordRef({} as OntologyGraph, "mat:UO2")).toBeNull();
    expect(extractRecordRef({ nodes: [] }, "mat:UO2")).toBeNull();
  });
});
