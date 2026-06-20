import { cp, rm, mkdir } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, "../..");
const distDir = resolve(projectRoot, "web/dist");
const targetDir = resolve(projectRoot, "src/web");

async function main() {
  await rm(targetDir, { recursive: true, force: true });
  await mkdir(targetDir, { recursive: true });
  await cp(distDir, targetDir, { recursive: true });
  console.log(`Copied ${distDir} -> ${targetDir}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
