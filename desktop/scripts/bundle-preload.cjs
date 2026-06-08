const path = require("node:path");

const esbuild = require("esbuild");

const desktopRoot = path.resolve(__dirname, "..");
const entryPoint = path.join(desktopRoot, "src", "preload", "preload.ts");
const outfile = path.join(desktopRoot, "dist", "preload", "preload.js");

if (!entryPoint.startsWith(path.join(desktopRoot, "src") + path.sep)) {
  throw new Error(`Refusing to bundle preload entry outside desktop src: ${entryPoint}`);
}
if (!outfile.startsWith(path.join(desktopRoot, "dist") + path.sep)) {
  throw new Error(`Refusing to write preload bundle outside desktop dist: ${outfile}`);
}

esbuild.buildSync({
  entryPoints: [entryPoint],
  outfile,
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  external: ["electron"],
  sourcemap: false,
  legalComments: "none",
  logLevel: "silent"
});

console.log(`Bundled sandbox-safe preload bridge to ${path.relative(desktopRoot, outfile).replace(/\\/g, "/")}`);
