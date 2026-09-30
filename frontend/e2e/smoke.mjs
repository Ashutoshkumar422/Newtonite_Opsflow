// End-to-end UI smoke test: two real browsers (a manager and a member) drive the running app.
//
// Prerequisites: the stack is running on a FRESHLY seeded database (python -m app.seed --reset),
// the outbox worker is running, and Playwright is installed:
//     npm i -D playwright && npx playwright install chromium
// Usage:
//     BASE_URL=http://localhost:5173 node e2e/smoke.mjs ./e2e-screenshots
// Set CHROME_PATH to use an existing Chromium binary instead of Playwright's download.
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const launchOpts = { headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) };
const launch = () => chromium.launch(launchOpts);

const out = process.argv[2] ?? "e2e-screenshots";
mkdirSync(out, { recursive: true });
const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const browser = await launch();
const results = [];
const check = (name, ok, extra = "") => { results.push({ name, ok, extra }); console.log(`${ok ? "PASS" : "FAIL"} ${name} ${extra}`); };

async function login(page, email) {
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill("#email", email);
  await page.fill("#password", "opsflow-demo");
  await page.click("button[type=submit]");
  await page.waitForURL(`${BASE}/`);
}

const ctxA = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const pageA = await ctxA.newPage();
const consoleErrors = [];
// Failed-resource logs for expected 4xx responses (401 on /auth/me, 409 conflicts) are not app errors.
pageA.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && consoleErrors.push(m.text()));

// 1. Unauthenticated → redirected to login
await pageA.goto(`${BASE}/work-items`);
await pageA.waitForURL(/\/login/);
check("unauthenticated redirect to login", pageA.url().includes("/login"));

// 2. Wrong password shows error
await pageA.fill("#email", "priya@opsflow.dev");
await pageA.fill("#password", "nope");
await pageA.click("button[type=submit]");
await pageA.waitForSelector("text=Invalid email or password");
check("invalid login shows error", true);
await pageA.screenshot({ path: `${out}/01-login.png` });

// 3. Login + dashboard
await login(pageA, "priya@opsflow.dev");
await pageA.waitForSelector("text=My active work");
await pageA.waitForTimeout(800);
await pageA.screenshot({ path: `${out}/02-dashboard.png`, fullPage: true });
check("dashboard renders", await pageA.isVisible("text=Awaiting your approval"));

// 4. List + search + filter
await pageA.goto(`${BASE}/work-items`);
await pageA.waitForSelector("table");
await pageA.fill("input[aria-label='Search work items']", "refund");
await pageA.waitForTimeout(900);
const rows = await pageA.locator("tbody tr").count();
check("search narrows list", rows >= 1 && rows < 10, `rows=${rows}`);
await pageA.screenshot({ path: `${out}/03-list-search.png` });
await pageA.goto(`${BASE}/work-items?status=blocked`);
await pageA.waitForSelector("table");
const blocked = await pageA.locator("tbody tr").allInnerTexts();
check("status filter via URL", blocked.every((t) => t.includes("Blocked")), `rows=${blocked.length}`);

// 5. Create item (form validation first)
await pageA.goto(`${BASE}/work-items/new`);
await pageA.click("button[type=submit]");
check("create form client validation", await pageA.isVisible("text=Choose a team") && await pageA.isVisible("text=A short title is required"));
await pageA.selectOption("#team", { label: "Payments (PAY)" });
await pageA.fill("#title", "UI smoke: settlement file missing for 29 Sep");
await pageA.fill("#description", "Created by the automated UI smoke test.");
await pageA.selectOption("#priority", "high");
await pageA.click("button[type=submit]");
await pageA.waitForURL(/\/work-items\/\d+$/);
const itemUrl = pageA.url();
check("create item navigates to detail", true, itemUrl);
await pageA.waitForSelector("text=UI smoke: settlement file missing");

// 6. Two browsers: Omar claims, then Priya (stale) edits → conflict UI
// Separate browser processes per user (some Chromium builds share cookies across contexts).
const browserB = await launch();
const ctxB = await browserB.newContext({ viewport: { width: 1400, height: 900 } });
const pageB = await ctxB.newPage();
await login(pageB, "omar@opsflow.dev");
await pageB.goto(itemUrl);
await pageB.waitForSelector("button:has-text('Claim')");

// Priya opens the editor on version 1 before Omar acts
await pageA.click("button:has-text('Edit')");
await pageA.fill("#edit-title", "UI smoke: settlement file missing (Priya's wording)");

await pageB.click("button:has-text('Claim')");
await pageB.waitForSelector("text=You now own this item");
await pageB.screenshot({ path: `${out}/04-claimed-by-omar.png` });
check("claim succeeds for first claimer", true);

await pageA.click("button:has-text('Save changes')");
await pageA.waitForSelector("text=Your changes were not saved");
await pageA.screenshot({ path: `${out}/05-conflict.png`, fullPage: true });
check("stale edit shows conflict panel with both versions", await pageA.isVisible("text=Apply my changes on top of latest"));
await pageA.click("button:has-text('Apply my changes on top of latest')");
await pageA.waitForSelector("h1:has-text(\"Priya's wording\")");
check("re-apply after conflict saves user's edit", true);

// 7. Omar starts work; blocked needs reason (modal)
await pageB.reload();
await pageB.click("button:has-text('Start work')");
await pageB.waitForSelector("text=moved to In progress");
await pageB.click("button:has-text('Mark blocked')");
await pageB.waitForSelector("role=dialog");
const submitDisabled = await pageB.isDisabled("role=dialog >> button[type=submit]");
check("reason-required transition disables submit until reason given", submitDisabled);
await pageB.fill("#reason", "Waiting for the bank to resend the file");
await pageB.click("role=dialog >> button[type=submit]");
await pageB.waitForSelector("text=moved to Blocked");
await pageB.click("button:has-text('Unblock')");
await pageB.waitForSelector("text=moved to In progress");
await pageB.click("button:has-text('Resolve')");
await pageB.waitForSelector("text=moved to Awaiting approval");
check("Omar (member) cannot approve", !(await pageB.isVisible("button:has-text('Approve & close')")));

// 8. Comment + history
await pageB.fill("textarea[aria-label='Add a comment']", "File received and reprocessed.");
await pageB.click("button:has-text('Comment')");
await pageB.waitForSelector("text=File received and reprocessed.");
await pageB.click("role=tab[name='History']");
await pageB.waitForSelector("text=Reason: Waiting for the bank");
await pageB.screenshot({ path: `${out}/06-history.png`, fullPage: true });
check("history shows transitions with reasons", true);

// 9. Priya approves
await pageA.reload();
await pageA.click("button:has-text('Approve & close')");
await pageA.waitForSelector("text=moved to Closed");
check("manager approves and closes", await pageA.isVisible("text=Closed"));
await pageA.screenshot({ path: `${out}/07-closed.png` });

// 10. Cross-team: Omar is not in Platform Engineering. Look up ENG-1's id as the admin via the API.
const adminToken = (
  await (await fetch(`${BASE}/api/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "admin@opsflow.dev", password: "opsflow-demo" }),
  })).json()
).access_token;
const engItem = (
  await (await fetch(`${BASE}/api/v1/work-items?q=ENG-1`, { headers: { Authorization: `Bearer ${adminToken}` } })).json()
).items[0];
await pageB.goto(`${BASE}/work-items/${engItem.id}`);
await pageB.waitForSelector("text=Work item not found");
check("cross-team item shows not found (server 404)", true);

// 11. Notifications (worker must be running for this to populate)
await pageB.waitForTimeout(3000);
await pageB.goto(`${BASE}/`);
await pageB.click("button[aria-label^='Notifications']");
await pageB.waitForTimeout(500);
await pageB.screenshot({ path: `${out}/08-notifications.png` });

// 12. Teams
await pageA.goto(`${BASE}/teams`);
await pageA.waitForSelector("text=Payments");
await pageA.click("text=Payments");
await pageA.waitForSelector("text=Active work");
await pageA.waitForTimeout(500);
await pageA.screenshot({ path: `${out}/09-team.png`, fullPage: true });
check("team page renders", true);

// 13. Mobile layout
await browserB.close();
const browserC = await launch();
const mob = await browserC.newContext({ viewport: { width: 390, height: 844 } });
const pm = await mob.newPage();
await login(pm, "priya@opsflow.dev");
await pm.waitForSelector("text=My active work");
await pm.screenshot({ path: `${out}/10-mobile.png`, fullPage: false });
check("mobile dashboard renders", true);


// ================================================================ identity & administration
async function freshPage(width = 1400) {
  const b = await launch();
  const ctx = await b.newContext({ viewport: { width, height: 900 } });
  return { b, page: await ctx.newPage() };
}
async function passwordLogin(page, email, password) {
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click("form button[type=submit]");
}

// 14. Admin creates a user; the one-time temporary password is shown once
const admin = await freshPage();
await login(admin.page, "admin@opsflow.dev");
await admin.page.goto(`${BASE}/admin`);
await admin.page.waitForSelector("text=New user");
await admin.page.click("button:has-text('New user')");
const stamp = Date.now();
const newEmail = `e2e.newcomer.${stamp}@opsflow.dev`;
await admin.page.fill("#nu-email", newEmail);
await admin.page.fill("#nu-name", "Eve Newcomer");
await admin.page.click("role=dialog >> button[type=submit]");
await admin.page.waitForSelector("[data-testid=temporary-password]");
const tempPassword = (await admin.page.textContent("[data-testid=temporary-password]")).trim();
await admin.page.screenshot({ path: `${out}/11-admin-temp-password.png` });
check("admin creates user and sees one-time temporary password", tempPassword.length >= 16);
await admin.page.click("role=dialog >> button:has-text('Done')");
await admin.page.fill("input[aria-label='Search users']", "newcomer");
await admin.page.waitForSelector(`text=${newEmail}`);
check("new user listed with temporary-password flag", await admin.page.isVisible("text=Temp password"));
await admin.page.screenshot({ path: `${out}/12-admin-users.png`, fullPage: true });

// 15. First sign-in forces a password change (enforced by the API, reflected by the UI)
const eve = await freshPage();
await passwordLogin(eve.page, newEmail, tempPassword);
await eve.page.waitForURL(`${BASE}/account`);
await eve.page.waitForSelector("text=Choose a new password to continue");
check("temporary password forces the account page", true);
await eve.page.goto(`${BASE}/work-items`);
await eve.page.waitForURL(`${BASE}/account`);
check("other pages are unreachable until password changed", true);
const blockedStatus = await eve.page.evaluate(async () => (await fetch("/api/v1/work-items")).status);
check("API itself refuses work endpoints with a temporary password", blockedStatus === 403, `status=${blockedStatus}`);
await eve.page.screenshot({ path: `${out}/13-forced-password-change.png` });
await eve.page.fill("#cur", tempPassword);
await eve.page.fill("#new", "amber-kettle-orbit-73");
await eve.page.fill("#confirm", "amber-kettle-orbit-73");
await eve.page.click("button:has-text('Update password')");
await eve.page.waitForSelector("text=Where you're signed in");
await eve.page.goto(`${BASE}/`);
await eve.page.waitForSelector("text=My active work");
check("after changing password the app is usable", true);

// 16. Sessions: sign out another device, which is then rejected immediately
const eve2 = await freshPage();
await passwordLogin(eve2.page, newEmail, "amber-kettle-orbit-73");
await eve2.page.waitForURL(`${BASE}/`);
await eve.page.goto(`${BASE}/account`);
await eve.page.waitForSelector("button:has-text('Sign out other devices')");
await eve.page.screenshot({ path: `${out}/14-account-sessions.png`, fullPage: true });
await eve.page.click("button:has-text('Sign out other devices')");
await eve.page.click("role=dialog >> button:has-text('Sign out others')");
await eve.page.waitForSelector("text=Signed out 1 other session");
await eve2.page.goto(`${BASE}/work-items`);
await eve2.page.waitForURL(/\/login/);
check("revoked device is signed out on its next request", true);
await eve2.b.close();

// 17. Deactivation ends every session at once
await admin.page.reload();
await admin.page.fill("input[aria-label='Search users']", "newcomer");
await admin.page.waitForSelector(`text=${newEmail}`);
await admin.page.click("button[aria-label='Actions for Eve Newcomer']");
await admin.page.click("role=menuitem >> text=Deactivate");
await admin.page.click("role=dialog >> button:has-text('Deactivate')");
await admin.page.waitForSelector("text=Deactivated");
await eve.page.goto(`${BASE}/teams`);
await eve.page.waitForURL(/\/login/);
check("deactivated user is thrown out immediately", true);
await eve.b.close();

// 18. Admin creates a team from the UI
await admin.page.goto(`${BASE}/teams`);
await admin.page.click("button:has-text('New team')");
const teamKey = `E${String(stamp).slice(-5)}`;
await admin.page.fill("#team-name", `E2E Team ${teamKey}`);
await admin.page.fill("#team-key", teamKey.toLowerCase());
await admin.page.click("role=dialog >> button[type=submit]");
await admin.page.waitForURL(/\/teams\/\d+$/);
await admin.page.waitForSelector(`text=E2E Team ${teamKey}`);
check("admin creates a team via the UI (key upper-cased)", await admin.page.isVisible(`text=${teamKey}`));

// 19. Security log shows what happened
await admin.page.goto(`${BASE}/admin`);
await admin.page.click("role=tab[name='Security log']");
await admin.page.waitForSelector("td:has-text('User deactivated')");
await admin.page.screenshot({ path: `${out}/15-security-log.png`, fullPage: true });
check("security log records account events", await admin.page.isVisible("td:has-text('Password changed')"));
await admin.b.close();

// 20. Login rate limiting
const spray = await freshPage();
for (let i = 0; i < 5; i++) {
  await passwordLogin(spray.page, "tom@opsflow.dev", `wrong-${i}`);
  await spray.page.waitForSelector("text=Invalid email or password");
}
await passwordLogin(spray.page, "tom@opsflow.dev", "opsflow-demo");
await spray.page.waitForSelector("text=Too many failed attempts");
await spray.page.screenshot({ path: `${out}/16-rate-limited.png` });
check("6th attempt is refused even with the right password", true);
await spray.b.close();

// 21. Single sign-on (only when configured)
const cfg = await (await fetch(`${BASE}/api/v1/auth/config`)).json();
if (cfg.sso_enabled) {
  const sso = await freshPage();
  await sso.page.goto(`${BASE}/login`);
  await sso.page.click(`text=Continue with ${cfg.sso_provider_name}`);
  await sso.page.waitForSelector("text=Development identity provider");
  await sso.page.fill("#email", "daniel@opsflow.dev");
  await sso.page.screenshot({ path: `${out}/17-sso-provider.png` });
  await sso.page.click("button:has-text('Continue')");
  await sso.page.waitForURL(`${BASE}/`);
  await sso.page.waitForSelector("text=Good to see you, Daniel");
  check("SSO sign-in lands on the dashboard as the linked user", true);
  const method = await sso.page.evaluate(async () => (await (await fetch("/api/v1/auth/me")).json()).auth_method);
  check("session created by SSO", method === "sso", method);
  await sso.b.close();

  const stranger = await freshPage();
  await stranger.page.goto(`${BASE}/login`);
  await stranger.page.click(`text=Continue with ${cfg.sso_provider_name}`);
  await stranger.page.fill("#email", "stranger@elsewhere.dev");
  await stranger.page.click("button:has-text('Continue')");
  await stranger.page.waitForSelector("text=There is no OpsFlow account for that email");
  await stranger.page.screenshot({ path: `${out}/18-sso-no-account.png` });
  check("SSO for an unknown email is refused with a clear message", true);
  await stranger.b.close();
} else {
  check("SSO flow (skipped: SSO not configured)", true);
}

check("no console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
await browser.close();
await browserC.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} UI checks passed`);
process.exit(failed.length ? 1 : 0);
