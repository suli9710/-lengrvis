// Publishing is intentionally workflow-only.  A local build cannot prove that
// it is the immutable, signed candidate linked to independently reviewed
// evidence, so it must never mutate a GitHub release.
console.error("Direct release publishing is disabled. Use the protected release-publish GitHub Actions workflow.");
process.exitCode = 2;
