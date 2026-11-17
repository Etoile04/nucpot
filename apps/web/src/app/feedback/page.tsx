import FeedbackView from "./FeedbackView"

// NFM-5418: the actual view is a Client Component ("use client"), and
// route segment config exports from client modules are compiled to
// throwing client stubs — exporting `revalidate` there fails the build.
// A thin server page is the vehicle: it emits the segment config while
// rendering the same view. Without it, this fully-static page defaults to
// s-maxage=31536000, letting the Cloudflare edge pin the HTML for a year
// so any deploy that changes chunk hashes orphans the cached refs
// (NFM-5335 class). Matches the home page's ISR window (NFM-5267).
export const revalidate = 300

export default function FeedbackPage() {
  return <FeedbackView />
}
