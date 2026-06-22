const { readFileSync } = require("node:fs");
const { join } = require("node:path");

// Fail-closed release version consistency gate.
//
// Ensures the published desktop version matches the release tag so that
// electron-updater's GitHub Releases feed resolves the correct update. A tag
// that disagrees with package.json `version` silently breaks auto-update, so
// this gate runs as part of `npm run dist:publish` before electron-builder
// uploads any assets.
//
// Tag sources, in priority order:
//   1. --tag <value> (or --tag=<value>) CLI argument
//   2. RELEASE_TAG environment variable
//   3. GITHUB_REF (only when it is a refs/tags/* ref)
//
// When no tag source is present this is treated as a local publish where
// electron-builder derives the release name from the package version; the
// check passes with a notice unless a tag is explicitly required via
// RELEASE_REQUIRE_TAG=1 or --require-tag.

function parseArgs(argv) {
  const parsed = { tag: undefined, requireTag: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--tag") {
      parsed.tag = argv[i + 1];
      i += 1;
    } else if (arg.startsWith("--tag=")) {
      parsed.tag = arg.slice("--tag=".length);
    } else if (arg === "--require-tag") {
      parsed.requireTag = true;
    }
  }
  return parsed;
}

function normalizeTag(raw) {
  if (typeof raw !== "string") {
    return undefined;
  }
  const trimmed = raw.trim().replace(/^refs\/tags\//, "");
  return trimmed === "" ? undefined : trimmed;
}

function resolveTag(cliTag) {
  const fromCli = normalizeTag(cliTag);
  if (fromCli) {
    return fromCli;
  }
  const fromEnv = normalizeTag(process.env.RELEASE_TAG);
  if (fromEnv) {
    return fromEnv;
  }
  const ref = (process.env.GITHUB_REF || "").trim();
  if (ref.startsWith("refs/tags/")) {
    return ref.replace(/^refs\/tags\//, "");
  }
  return undefined;
}

const { tag: cliTag, requireTag } = parseArgs(process.argv.slice(2));
const tagRequired = requireTag || process.env.RELEASE_REQUIRE_TAG === "1";

const packagePath = join(__dirname, "..", "package.json");
const pkg = JSON.parse(readFileSync(packagePath, "utf8"));
const version = typeof pkg.version === "string" ? pkg.version.trim() : "";

const issues = [];

const semverPattern = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
if (!semverPattern.test(version)) {
  issues.push(`package.json version is not a valid SemVer string: "${version}"`);
}

const tag = resolveTag(cliTag);
if (!tag) {
  if (tagRequired) {
    issues.push(
      "No release tag found (checked --tag, RELEASE_TAG, and refs/tags/* in GITHUB_REF) but a tag is required."
    );
  }
} else {
  const expectedTag = `v${version}`;
  if (tag !== expectedTag) {
    issues.push(
      `Release tag "${tag}" does not match package.json version "${version}" (expected tag "${expectedTag}").`
    );
  }
}

if (issues.length > 0) {
  console.error("Release version consistency check failed:");
  for (const issue of issues) {
    console.error(` - ${issue}`);
  }
  console.error(
    "The git tag must equal `v<version>` from desktop/package.json so electron-updater resolves the correct GitHub Release."
  );
  process.exit(1);
}

if (tag) {
  console.log(`Release version verified: tag ${tag} matches package.json version ${version}.`);
} else {
  console.log(
    `Release version format verified (${version}); no release tag provided, skipping tag match. ` +
      "Set RELEASE_TAG or pass --tag to enforce the tag/version match."
  );
}
