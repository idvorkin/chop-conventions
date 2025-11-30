When running dev servers in containers with Tailscale, configure Vite to:

1. **Detect container environment** - Check for `/.dockerenv` or `container` env var
2. **Bind to 0.0.0.0** - Required for Tailscale routing to work
3. **Add Tailscale hostnames to allowedHosts** - Vite 6.x blocks requests from unknown hosts

Example for `vite.config.ts`:

```ts
import { execSync } from "child_process";
import { existsSync } from "fs";

// Detect if running in a container
function isContainer(): boolean {
	return existsSync("/.dockerenv") || process.env.container !== undefined;
}

// Detect Tailscale IP if available
function getTailscaleIP(): string | null {
	try {
		const ip = execSync("tailscale ip -4 2>/dev/null", { encoding: "utf-8" }).trim();
		return ip || null;
	} catch {
		return null;
	}
}

// Get Tailscale hostnames (short like "c-5002" and full like "c-5002.squeaker-teeth.ts.net")
function getTailscaleHostnames(): { short: string; full: string } | null {
	try {
		const json = execSync("tailscale status --json 2>/dev/null", { encoding: "utf-8" });
		const status = JSON.parse(json);
		const fullName = status.Self?.DNSName?.replace(/\.$/, "");
		if (!fullName) return null;
		const shortName = fullName.split(".")[0];
		return { short: shortName, full: fullName };
	} catch {
		return null;
	}
}

const inContainer = isContainer();
const tailscaleIP = getTailscaleIP();
const tailscaleHosts = getTailscaleHostnames();
const devHost = inContainer && tailscaleIP ? "0.0.0.0" : "localhost";

export default defineConfig({
	server: {
		host: devHost,
		allowedHosts: tailscaleHosts ? [tailscaleHosts.short, tailscaleHosts.full] : undefined,
	},
});
```

This enables accessing the dev server from other Tailscale devices via MagicDNS (e.g., `http://c-5002:3000/`).

Reference implementation: https://github.com/idvorkin/humane-tracker-1/pull/25
