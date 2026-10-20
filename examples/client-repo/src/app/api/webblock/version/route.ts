// Deployment verification endpoint polled by the harness (DEPLOY_VERIFY_URL_TEMPLATE).
// Reports the commit the running build was produced from.
export const dynamic = "force-static";

export function GET() {
  return Response.json({ commit_sha: process.env.WEBBLOCK_COMMIT_SHA ?? null });
}
