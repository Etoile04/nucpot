/**
 * Review API — re-exports from the canonical kg-review-api module.
 *
 * All KG Review and Conflict Resolution API functions live in
 * @/lib/kg-review-api. This module provides a shorter import path
 * and can add review-specific higher-level helpers later.
 */

export {
  fetchKgReviewQueue,
  batchKgReview,
  fetchConflicts,
  resolveConflict,
  type KgReviewItem,
  type ConflictSource,
  type ConflictItem,
  type ConflictResolutionAction,
} from "@/lib/kg-review-api"
