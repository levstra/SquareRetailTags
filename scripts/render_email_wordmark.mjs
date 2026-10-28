// Renders the "Levstra" wordmark used at the top of Cognito emails
// (src/esl/accounts/cognito_emails.py) as a 2x PNG.
//
//   node scripts/render_email_wordmark.mjs infra/prod/branding/email-wordmark.png
//
// Rerun only when the wordmark changes, then set WORDMARK_WIDTH in
// cognito_emails.py to half the new PNG width (tests check it). The renderer
// and the font are fetched into a temp folder, so the project gains no
// dependency.
import { execSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const out = process.argv[2];
if (!out) {
  console.error("usage: node scripts/render_email_wordmark.mjs <out.png>");
  process.exit(1);
}

const HEIGHT = 56; // 2x the 28px the email displays
const PAD = 2; // px at 2x around the glyphs
const ACCENT = "#6d3fd4";

const work = mkdtempSync(join(tmpdir(), "levstra-wordmark-"));
execSync("npm init -y && npm install --no-audit --no-fund @resvg/resvg-js@2.6.2", {
  cwd: work,
  stdio: "ignore",
});
const { Resvg } = createRequire(join(work, "package.json"))("@resvg/resvg-js");

// Without a browser user agent Google Fonts serves static TTF instances,
// which resvg can load (the site loads the same family and weight).
const css = await (
  await fetch("https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@700")
).text();
const fontUrl = css.match(/url\((https:[^)]+\.ttf)\)/)[1];
const fontFile = join(work, "PlusJakartaSans-Bold.ttf");
writeFileSync(fontFile, Buffer.from(await (await fetch(fontUrl)).arrayBuffer()));

// Bold with the site's tracking-tight (-0.025em), as in App.tsx's Logo.
const text = (viewBox) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}">
  <text x="0" y="100" font-family="Plus Jakarta Sans" font-weight="700" font-size="100"
        letter-spacing="-2.5" fill="${ACCENT}">Levstra</text>
</svg>`;
const font = { fontFiles: [fontFile], loadSystemFonts: false, defaultFontFamily: "Plus Jakarta Sans" };

// First pass: measure the ink, then render just that box at HEIGHT.
const box = new Resvg(text("0 0 600 160"), { font }).innerBBox();
const pad = (PAD * box.height) / (HEIGHT - 2 * PAD);
const viewBox = [box.x - pad, box.y - pad, box.width + 2 * pad, box.height + 2 * pad].join(" ");
const png = new Resvg(text(viewBox), { font, fitTo: { mode: "height", value: HEIGHT } })
  .render()
  .asPng();
writeFileSync(resolve(out), png);
console.log(`wrote ${out}: ${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`);
