// Owned by spec-harness.
export default async function specHook(input: { event: string; [key: string]: unknown }) {
  const { spawnSync } = await import("node:child_process");
  const result = spawnSync("python3", [new URL("./spec_hook.py", import.meta.url).pathname, "--host", "opencode", input.event], { input: JSON.stringify(input), encoding: "utf8" });
  return result.stdout ? JSON.parse(result.stdout) : {};
}
