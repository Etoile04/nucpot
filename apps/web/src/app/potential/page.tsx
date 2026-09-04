import { redirect } from "next/navigation"

// BUG-10 (NFM-4086): /potential had no top-level route (only
// /potential/[id] detail pages exist), so bare /potential links 404'd.
// Redirect to the potentials browse listing which is the canonical
// entry point for the potential-function library.
export default function PotentialIndexPage() {
  redirect("/browse")
}
