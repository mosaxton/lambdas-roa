/**
 * Generates cross-language test fixtures for the AES-256-GCM encryption contract.
 *
 * Encrypts a known plaintext using the real TypeScript encryption module from the
 * web app, then writes the raw bytes + metadata to shared/tests/fixtures/.
 *
 * This script is run manually when fixtures need regeneration. The committed
 * output is the stable contract that Python tests decrypt against.
 *
 * Usage:
 *   npx tsx scripts/generate-cross-lang-fixture.ts
 */

import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

// ─── Hardcoded test key (safe to commit — encrypts only synthetic test data) ───
const TEST_KEY_HEX =
  "5c3d4a2b1f8e7d6c9b0a1234567890abcdef0123456789abcdef0123456789ab";

// ─── Test plaintext includes unicode to prove UTF-8 handling ────────────────
const PLAINTEXT = "The quick brown fox jumps over the lazy dog — 🦊";

// ─── Resolve paths relative to repo root ────────────────────────────────────
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = join(__dirname, "..");
const FIXTURES_DIR = join(REPO_ROOT, "shared", "tests", "fixtures");
const BIN_PATH = join(FIXTURES_DIR, "ts_encrypted.bin");
const META_PATH = join(FIXTURES_DIR, "ts_encrypted_meta.json");

// ─── Set ENCRYPTION_KEY before importing the module ─────────────────────────
process.env.ENCRYPTION_KEY = TEST_KEY_HEX;

// Dynamic import so env var is set first
const { encrypt } = await import(
  "../vendor/app.rightofaccess/lib/encryption.ts"
);

// ─── Encrypt and write output ────────────────────────────────────────────────
const encryptedBuffer: Buffer = encrypt(PLAINTEXT);

mkdirSync(FIXTURES_DIR, { recursive: true });
writeFileSync(BIN_PATH, encryptedBuffer);

const meta = {
  plaintext: PLAINTEXT,
  key_hex: TEST_KEY_HEX,
  generated_by: "scripts/generate-cross-lang-fixture.ts",
  generated_at: new Date().toISOString(),
  ts_source_path: "vendor/app.rightofaccess/lib/encryption.ts",
};
writeFileSync(META_PATH, JSON.stringify(meta, null, 2) + "\n", "utf-8");

// Log to stderr so stdout stays clean
process.stderr.write(
  `wrote shared/tests/fixtures/ts_encrypted.bin (${encryptedBuffer.length} bytes)\n`
);
process.stderr.write(`wrote shared/tests/fixtures/ts_encrypted_meta.json\n`);
